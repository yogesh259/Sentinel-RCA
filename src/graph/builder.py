"""Wires the RCA nodes into a LangGraph pipeline and holds the app's compiled graph."""

from collections.abc import Awaitable, Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.graph.nodes import (
    authorisation_gate_node,
    dedup_node,
    execute_node,
    investigate_git_node,
    parse_trace_node,
)
from src.graph.state import RCAState
from src.services.mcp_client import record_tool_calls

NodeFn = Callable[[RCAState], Awaitable[dict]]

_graph: CompiledStateGraph | None = None


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


def build_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    """Compile the five-step RCA pipeline on top of the given checkpointer."""
    builder = StateGraph(RCAState)

    builder.add_node("parse_trace", audited("parse_trace", parse_trace_node))
    builder.add_node("dedup", audited("dedup", dedup_node))
    builder.add_node("investigate_git", audited("investigate_git", investigate_git_node))
    builder.add_node("authorisation_gate", audited("authorisation_gate", authorisation_gate_node))
    builder.add_node("execute_action", audited("execute_action", execute_node))

    builder.add_edge(START, "parse_trace")
    builder.add_edge("parse_trace", "dedup")
    builder.add_edge("dedup", "investigate_git")
    builder.add_edge("investigate_git", "authorisation_gate")
    builder.add_edge("authorisation_gate", "execute_action")
    builder.add_edge("execute_action", END)

    return builder.compile(checkpointer=checkpointer)


def init_graph(checkpointer: BaseCheckpointSaver) -> None:
    """Build the app-wide graph once the checkpointer is open (called from the lifespan)."""
    global _graph
    _graph = build_graph(checkpointer)


def get_graph() -> CompiledStateGraph:
    """Return the app-wide graph built by init_graph()."""
    if _graph is None:
        raise RuntimeError("RCA graph is not initialised; init_graph() runs in the app lifespan.")
    return _graph
