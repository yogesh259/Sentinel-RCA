"""StateGraph wiring, edges, and checkpointer management."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.graph.nodes import (
    authorisation_gate_node,
    dedup_node,
    execute_node,
    investigate_git_node,
    parse_trace_node,
)
from src.graph.state import RCAState
from src.services.mcp_client import record_tool_calls

logger = logging.getLogger("sentinel-rca.graph.builder")

_active_checkpointer: BaseCheckpointSaver | None = None
_compiled_graph: Any = None


def get_default_checkpointer() -> BaseCheckpointSaver:
    """Return the active default in-memory checkpointer."""
    global _active_checkpointer
    if _active_checkpointer is None:
        _active_checkpointer = MemorySaver()
    return _active_checkpointer


def set_default_checkpointer(checkpointer: BaseCheckpointSaver) -> None:
    """Configure the active checkpointer and invalidate cached compiled graph."""
    global _active_checkpointer, _compiled_graph
    _active_checkpointer = checkpointer
    _compiled_graph = None


NodeFn = Callable[[RCAState], Awaitable[dict]]


def audited(name: str, node: NodeFn) -> NodeFn:
    """Wrap a node so each completed run appends its name and real MCP tool calls.

    Done once here rather than in each node, so the audit trail can't be forgotten.
    """

    async def run(state: RCAState) -> dict:
        with record_tool_calls() as tool_calls:
            update = await node(state)
        return {**update, "nodes_visited": [name], "tool_calls": tool_calls}

    run.__name__ = name
    return run


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compile and return the stateful RCA LangGraph."""
    cp = checkpointer or get_default_checkpointer()

    builder = StateGraph(RCAState)

    builder.add_node("parse_trace", audited("parse_trace", parse_trace_node))
    builder.add_node("dedup", audited("dedup", dedup_node))
    builder.add_node("investigate_git", audited("investigate_git", investigate_git_node))
    builder.add_node("authorisation_gate", audited("authorisation_gate", authorisation_gate_node))
    builder.add_node("execute_action", audited("execute_action", execute_node))

    builder.set_entry_point("parse_trace")
    builder.add_edge("parse_trace", "dedup")
    builder.add_edge("dedup", "investigate_git")
    builder.add_edge("investigate_git", "authorisation_gate")
    builder.add_edge("authorisation_gate", "execute_action")
    builder.add_edge("execute_action", END)

    return builder.compile(checkpointer=cp)


def get_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Return the active compiled graph singleton or build new one."""
    global _compiled_graph
    if _compiled_graph is None or checkpointer is not None:
        _compiled_graph = build_graph(checkpointer)
    return _compiled_graph


def reset_graph() -> None:
    """Reset the compiled graph and checkpointer for clean test isolation."""
    global _compiled_graph, _active_checkpointer
    _compiled_graph = None
    _active_checkpointer = None


__all__ = [
    "build_graph",
    "get_default_checkpointer",
    "get_graph",
    "reset_graph",
    "set_default_checkpointer",
]
