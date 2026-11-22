"""MCP client layer: the only way graph nodes reach tools.

Each tool server runs as its own stdio subprocess that receives only the secrets it
needs (git: GitHub token; tracker: database URL; trace: nothing). LLM keys never reach
tool processes. Every call is timed and recorded so execution traces show the real
tool trajectory instead of a reconstructed one.
"""

import logging
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal, TypeVar

from mcp import Client, StdioServerParameters
from pydantic import BaseModel, TypeAdapter

from src.core.config import config

logger = logging.getLogger("sentinel-rca.mcp_client")

ServerName = Literal["trace", "git", "tracker"]
T = TypeVar("T")

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_AUDIT_ARG_CHARS = 200

SERVER_MODULES: dict[str, str] = {
    "trace": "src.mcp_servers.trace_mcp",
    "git": "src.mcp_servers.git_mcp",
    "tracker": "src.mcp_servers.tracker_mcp",
}

# Persistent sessions opened for the app's lifetime (see open_mcp_sessions).
_sessions: dict[str, Client] = {}
# Per-node log of tool calls; the graph builder opens one around every node run.
_tool_log: ContextVar[list[dict] | None] = ContextVar("mcp_tool_log", default=None)


class ToolCallError(RuntimeError):
    """Raised when an MCP tool reports an error or returns an unexpected shape."""


def server_secrets(name: str) -> dict[str, str]:
    """Least-privilege environment for one tool subprocess."""
    if name == "git":
        return {
            "GITHUB_TOKEN": config.github_token.get_secret_value(),
            "GITHUB_REPO": config.github_repo,
        }
    if name == "tracker":
        return {"DATABASE_URL": config.database_url.get_secret_value()}
    return {}


def _server_target(name: str) -> Any:
    """What `mcp.Client` connects to for *name* under the configured transport."""
    module = SERVER_MODULES[name]
    if config.mcp_transport == "inprocess":
        return __import__(module, fromlist=["server"]).server
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        env={"PYTHONPATH": str(REPO_ROOT), **server_secrets(name)},
        # Outside the repo so the subprocess can't pick up the API's .env file.
        cwd=tempfile.gettempdir(),
    )


@asynccontextmanager
async def open_mcp_sessions() -> AsyncIterator[None]:
    """Start every tool server once and keep its session for the app's lifetime."""
    async with AsyncExitStack() as stack:
        for name in SERVER_MODULES:
            _sessions[name] = await stack.enter_async_context(Client(_server_target(name)))
        logger.info("MCP servers connected over %s: %s", config.mcp_transport, ", ".join(_sessions))
        try:
            yield
        finally:
            _sessions.clear()


@contextmanager
def record_tool_calls() -> Iterator[list[dict]]:
    """Collect every tool call made inside the block (including in gathered sub-tasks)."""
    log: list[dict] = []
    token = _tool_log.set(log)
    try:
        yield log
    finally:
        _tool_log.reset(token)


def _audit_args(args: dict[str, Any]) -> dict[str, Any]:
    """Truncate long arguments (e.g. whole stack traces) for the audit record."""
    return {
        k: v[:MAX_AUDIT_ARG_CHARS] if isinstance(v, str) else v for k, v in args.items()
    }


def _decode(result: Any, result_type: type[T]) -> T:
    """Validate a tool's structured output; non-model returns arrive as {"result": ...}."""
    payload = result.structured_content
    if payload is None:
        raise ToolCallError("Tool returned no structured content.")
    is_model = isinstance(result_type, type) and issubclass(result_type, BaseModel)
    return TypeAdapter(result_type).validate_python(payload if is_model else payload.get("result"))


async def _invoke(server: str, tool: str, args: dict[str, Any]) -> Any:
    """Call a tool on the persistent session, or a short-lived one outside the app."""
    if server in _sessions:
        return await _sessions[server].call_tool(tool, args)
    async with Client(_server_target(server)) as client:
        return await client.call_tool(tool, args)


async def call_tool(server: ServerName, tool: str, args: dict[str, Any], result_type: type[T]) -> T:
    """Call `server.tool(**args)` over MCP and return the validated result."""
    started = time.perf_counter()
    ok = False
    try:
        result = await _invoke(server, tool, args)
        if result.is_error:
            detail = " ".join(getattr(c, "text", "") for c in result.content)
            raise ToolCallError(f"{server}.{tool} failed: {detail}")
        value = _decode(result, result_type)
        ok = True
        return value
    finally:
        log = _tool_log.get()
        if log is not None:
            log.append(
                {
                    "server": f"{server}_mcp",
                    "tool": tool,
                    "args": _audit_args(args),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "ok": ok,
                }
            )


__all__ = ["ToolCallError", "call_tool", "open_mcp_sessions", "record_tool_calls"]
