"""Parse trace node: trace_mcp parsing + git_mcp repo path resolution (both over MCP)."""

import logging

from src.core.redaction import redact_dict, redact_payload
from src.graph.state import RCAState
from src.schemas.mcp.trace import ParsedStackTrace, StackFrame
from src.schemas.rca import TraceLocation
from src.services import llm_router
from src.services.mcp_client import call_tool
from src.services.payload_adapter import payload_to_trace
from src.services.rca_prompts import build_trace_messages

logger = logging.getLogger("sentinel-rca.nodes.parse_trace")

# Only the deepest few frames are worth resolving/blaming; outer frames are routing noise.
MAX_FRAMES = 3


async def resolve_frames(frames: list[StackFrame]) -> list[dict]:
    """Attach the repository path to each frame (None for library/unknown files).

    Resolution failures are logged, not raised: a parse result without repo paths is
    still useful for dedup and for a human reading the issue.
    """
    resolved: list[dict] = []
    for frame in frames[:MAX_FRAMES]:
        try:
            path = await call_tool(
                "git",
                "resolve_repo_path",
                {"file_name": frame.file, "module_hint": frame.module},
                str | None,
            )
        except Exception as exc:
            logger.warning("Path resolution failed for %s: %s", frame.file, exc)
            path = None
        resolved.append({**frame.model_dump(), "path": path})
    return resolved


async def llm_locate(parsed: ParsedStackTrace, raw: str) -> tuple[ParsedStackTrace, list[dict]]:
    """Ask the triage model to locate the failure when no regex parser recognised the format.

    Best-effort: on any LLM failure the original (frameless) parse is returned.
    """
    try:
        location, calls = await llm_router.complete_structured(
            "triage", build_trace_messages(raw), TraceLocation
        )
    except llm_router.LLMError as exc:
        logger.warning("Triage trace extraction unavailable: %s", exc)
        return parsed, []

    frames = [StackFrame(file=location.file, line=location.line, function=location.function)]
    update = {"frames": frames if location.file else []}
    if parsed.exception_type == "UnknownException":
        update |= {
            "exception_type": location.exception_type,
            "exception_message": location.exception_message,
        }
    return parsed.model_copy(update=update), [c.usage() for c in calls]


async def parse_trace_node(state: RCAState) -> dict:
    """Node 1: Parse the redacted stack trace and locate its frames in the repository."""
    payload, _ = redact_dict(state.get("raw_payload") or {})
    source = state.get("raw_trace", "") or payload_to_trace(payload)
    # Redact here, not only at the gateway: raw_trace is persisted in checkpoints and
    # quoted in the RCA prompt, so this node must never pass an unredacted trace on.
    raw, _ = redact_payload(source)

    parsed = await call_tool("trace", "parse_stack_trace", {"raw_trace": raw}, ParsedStackTrace)
    llm_calls: list[dict] = []
    if not parsed.frames and raw.strip():
        parsed, llm_calls = await llm_locate(parsed, raw)
    frames = await resolve_frames(parsed.frames)
    # The deepest frame that lives in the repo is the one we can actually blame.
    primary = next((f for f in frames if f["path"]), frames[0] if frames else {})

    return {
        "raw_payload": payload,
        "raw_trace": raw,
        "exception_type": parsed.exception_type,
        "exception_message": parsed.exception_message,
        "target_file": primary.get("path") or primary.get("file", ""),
        "line_number": primary.get("line", 0),
        "frames": frames,
        "llm_calls": llm_calls,
    }
