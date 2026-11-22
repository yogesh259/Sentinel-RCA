"""Wires the nodes into the Sentinel graph.

START → prepare_repo → parse_trace → orchestrator → (locator ∥ history) → rca_writer → reviewer
      → human_review → create_pr → END, with branches to fail, report_external and one locator retry.
"""

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.history import history_node
from src.agents.locator import locator_node
from src.agents.orchestrator import orchestrator_node
from src.agents.rca_writer import rca_writer_node
from src.agents.reviewer import reviewer_node
from src.graph import nodes
from src.graph.routing import route_after_decision, route_after_parse, route_after_prepare, route_after_review
from src.models.state import SentinelState

log = logging.getLogger(__name__)

# Our Pydantic state models, allow-listed so the checkpointer may restore them on resume.
CHECKPOINT_TYPES = [
    ("src.models.trace", "ParsedTrace"),
    ("src.models.trace", "Frame"),
    ("src.models.plan", "Plan"),
    ("src.models.finding", "Finding"),
    ("src.models.finding", "Evidence"),
    ("src.models.report", "RCAReport"),
    ("src.models.report", "SuspectLocation"),
    ("src.models.report", "ReviewResult"),
    ("src.models.report", "Decision"),
]


def new_run(repo_url: str, raw_trace: str, base_commit: str | None = None) -> SentinelState:
    """The initial state of a run; every key exists so nodes and routers never need .get()."""
    return SentinelState(
        repo_url=repo_url,
        base_commit=base_commit,
        raw_trace=raw_trace,
        repo_path=None,
        trace=None,
        plan=None,
        findings=[],
        report=None,
        review=None,
        retries=0,
        decision=None,
        pr_url=None,
        error=None,
    )


def build_graph(with_reviewer: bool = True) -> CompiledStateGraph:
    """Compile the graph. `with_reviewer=False` is the eval ablation without the reviewer and retry.

    InMemorySaver is only here because interrupt() needs a checkpointer to pause and resume;
    paused runs are lost on restart, by design.
    """
    graph = StateGraph(SentinelState)
    steps: dict[str, Callable[[SentinelState], dict]] = {
        "prepare_repo": nodes.prepare_repo_node,
        "parse_trace": nodes.parse_trace_node,
        "orchestrator": orchestrator_node,
        "locator": locator_node,
        "history": history_node,
        "rca_writer": rca_writer_node,
        "human_review": nodes.human_review_node,
        "create_pr": nodes.create_pr_node,
        "report_external": nodes.report_external_node,
        "fail": nodes.fail_node,
    }
    if with_reviewer:
        steps["reviewer"] = reviewer_node
    for name, node in steps.items():
        graph.add_node(name, logged(name, node))

    graph.add_edge(START, "prepare_repo")
    graph.add_conditional_edges("prepare_repo", route_after_prepare, ["fail", "parse_trace"])
    graph.add_conditional_edges("parse_trace", route_after_parse, ["fail", "report_external", "orchestrator"])
    # Two edges out of the orchestrator: locator and history run in parallel in the same step.
    graph.add_edge("orchestrator", "locator")
    graph.add_edge("orchestrator", "history")
    # rca_writer runs once after both finish, and again after a locator-only retry.
    graph.add_edge("locator", "rca_writer")
    graph.add_edge("history", "rca_writer")
    if with_reviewer:
        graph.add_edge("rca_writer", "reviewer")
        graph.add_conditional_edges("reviewer", route_after_review, ["locator", "human_review"])
    else:
        graph.add_edge("rca_writer", "human_review")
    graph.add_conditional_edges("human_review", route_after_decision, ["create_pr", END])
    for last in ("create_pr", "report_external", "fail"):
        graph.add_edge(last, END)

    serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
    return graph.compile(checkpointer=InMemorySaver(serde=serde))


def is_paused(graph: CompiledStateGraph, config: dict) -> bool:
    """True when the run is waiting at human_review. A crashed run also has a `next` node, so check interrupts."""
    return any(task.interrupts for task in graph.get_state(config).tasks)


def logged(name: str, node: Callable[[SentinelState], dict]) -> Callable[[SentinelState], dict]:
    """Log when each node starts and how long it took (token and cost logs come from the LLM client)."""

    @wraps(node)
    def run(state: SentinelState) -> dict[str, Any]:
        log.info("%s started", name)
        started = time.perf_counter()
        update = node(state)
        log.info("%s finished in %.1fs", name, time.perf_counter() - started)
        return update

    return run
