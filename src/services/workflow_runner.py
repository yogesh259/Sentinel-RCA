"""Starts and resumes LangGraph RCA runs and records each outcome in execution_traces."""

import logging
import time

from langgraph.types import Command

from src.graph import get_graph, get_pending_approval, thread_config
from src.services.execution_traces import (
    record_execution_trace,
    summarise_run,
    update_execution_trace_verdict,
)

logger = logging.getLogger("sentinel-rca.workflow_runner")


async def run_rca_workflow(
    thread_id: str,
    incident_id: int | None,
    fingerprint: str,
    redacted_payload: dict,
) -> None:
    """Run the RCA graph for one incident (as a background task) and record its trace.

    The payload must already be redacted by the gateway; it is persisted as-is.
    """
    logger.info("Running RCA workflow: thread_id=%s incident_id=%s", thread_id, incident_id)
    t0 = time.perf_counter()
    graph = get_graph()
    initial_state = {
        "thread_id": thread_id,
        "incident_id": incident_id,
        "fingerprint": fingerprint,
        "raw_payload": redacted_payload,
    }

    try:
        final_state = await graph.ainvoke(initial_state, config=thread_config(thread_id))
        paused = await get_pending_approval(graph, thread_id) is not None
        verdict = "AWAITING_APPROVAL" if paused else str(final_state.get("status") or "completed")
        summary = summarise_run(final_state)
    except Exception as exc:
        # Still record the run: a silently vanished incident is worse than a failed one.
        logger.error("RCA workflow failed for %s: %s", thread_id, exc, exc_info=True)
        verdict = "ERROR"
        summary = summarise_run({})

    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info("Workflow %s finished: verdict=%s in %.0fms", thread_id, verdict, latency_ms)
    await record_execution_trace(
        thread_id=thread_id,
        incident_id=incident_id,
        fingerprint=fingerprint,
        redacted_payload=redacted_payload,
        latency_ms=latency_ms,
        final_verdict=verdict,
        **summary,
    )


async def resume_workflow(thread_id: str, decision: str, reviewer_role: str) -> dict:
    """Feed a reviewer's decision into the paused interrupt() and update the audit row.

    RBAC is checked by the caller before this runs; here the decision is simply applied.
    """
    command = Command(resume={"decision": decision, "reviewer_role": reviewer_role})
    resumed_state = await get_graph().ainvoke(command, config=thread_config(thread_id))

    summary = summarise_run(resumed_state)
    await update_execution_trace_verdict(
        thread_id=thread_id,
        final_verdict="REJECTED" if decision == "reject" else "APPROVED",
        nodes_visited=summary["nodes_visited"],
        mcp_tool_calls=summary["mcp_tool_calls"],
    )
    return resumed_state


__all__ = ["resume_workflow", "run_rca_workflow"]
