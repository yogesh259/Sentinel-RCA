"""Execution-trace service: the audit trail of every RCA run.

Persists through the execution_traces repository, and keeps an in-memory copy of
recent traces so the Ops Console still works in local dev or when Postgres is down.
"""

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.db.session import session_scope
from src.repositories import execution_traces as repo
from src.schemas.trace import ExecutionTrace

logger = logging.getLogger("sentinel-rca.execution_traces")

MAX_IN_MEMORY_TRACES: int = 100
DEFAULT_TRACE_LIMIT: int = 20
# Database unavailable or failing: log and fall back, never fail the RCA run over audit.
DB_ERRORS = (SQLAlchemyError, OSError)

_recent_traces: deque[ExecutionTrace] = deque(maxlen=MAX_IN_MEMORY_TRACES)


def summarise_run(state: dict) -> dict:
    """Aggregate the audit records the graph nodes appended into execution_traces columns."""
    llm_calls = state.get("llm_calls", [])
    return {
        "nodes_visited": state.get("nodes_visited", []),
        "mcp_tool_calls": state.get("tool_calls", []),
        "tokens_used": sum(c["input_tokens"] + c["output_tokens"] for c in llm_calls),
        "cost_usd": sum(c["cost_usd"] for c in llm_calls),
        "models_used": ", ".join(sorted({c["model"] for c in llm_calls})) or "no_llm",
    }


async def record_execution_trace(
    thread_id: str,
    incident_id: int | None,
    fingerprint: str,
    redacted_payload: dict[str, Any],
    nodes_visited: list[str],
    mcp_tool_calls: list[dict[str, Any]],
    latency_ms: float,
    cost_usd: float,
    tokens_used: int,
    final_verdict: str,
    models_used: str = "",
) -> ExecutionTrace:
    """Store a finished run's trace in memory and, if reachable, in Postgres."""
    trace = ExecutionTrace(
        id=len(_recent_traces) + 1,
        thread_id=thread_id,
        incident_id=incident_id,
        fingerprint=fingerprint,
        redacted_payload=redacted_payload,
        nodes_visited=nodes_visited,
        mcp_tool_calls=mcp_tool_calls,
        latency_ms=round(latency_ms, 2),
        cost_usd=round(cost_usd, 6),
        tokens_used=tokens_used,
        models_used=models_used,
        final_verdict=final_verdict,
        created_at=datetime.now(UTC),
    )
    _recent_traces.appendleft(trace)

    try:
        async with session_scope() as session:
            row = await repo.add_trace(session, **trace.model_dump(exclude={"id"}))
        trace.id = row.id
    except DB_ERRORS as exc:
        logger.warning("Postgres unavailable for trace storage (%s), stored in-memory only", exc)

    return trace


async def update_execution_trace_verdict(
    thread_id: str,
    final_verdict: str,
    nodes_visited: list[str] | None = None,
    mcp_tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Record a resumed run's final verdict (and its extended audit lists, if given)."""
    changes: dict[str, Any] = {"final_verdict": final_verdict}
    if nodes_visited is not None:
        changes["nodes_visited"] = nodes_visited
    if mcp_tool_calls is not None:
        changes["mcp_tool_calls"] = mcp_tool_calls

    for trace in _recent_traces:
        if trace.thread_id == thread_id:
            for field, value in changes.items():
                setattr(trace, field, value)
            break

    try:
        async with session_scope() as session:
            await repo.update_trace(session, thread_id, **changes)
    except DB_ERRORS as exc:
        logger.warning("Failed updating trace verdict in Postgres (%s)", exc)


async def get_latest_execution_traces(limit: int = DEFAULT_TRACE_LIMIT) -> list[ExecutionTrace]:
    """Recent traces from Postgres, falling back to the in-memory copy."""
    try:
        async with session_scope() as session:
            rows = await repo.list_latest(session, limit)
        if rows:
            return [ExecutionTrace.model_validate(row) for row in rows]
    except DB_ERRORS as exc:
        logger.warning("Failed reading traces from Postgres (%s), using in-memory buffer", exc)

    return list(_recent_traces)[:limit]
