"""Postgres-backed observability layer for Sentinel-RCA.

Structured Execution Trace:
  Persists per-incident run telemetry (redacted payload, nodes visited,
  MCP tool calls, latency_ms, token cost, mode, final verdict) in Postgres
  with an in-memory deque fallback for local dev / offline mode / tests.
"""

import json
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

import asyncpg

from src.schemas.trace import ExecutionTrace
from src.services.postgres import get_pool

logger = logging.getLogger("sentinel-rca.observability")

# ── Constants ──────────────────────────────────────────────────────

MAX_IN_MEMORY_TRACES: int = 100
DEFAULT_TRACE_LIMIT: int = 20

DB_ERRORS = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
    ConnectionError,
    TimeoutError,
)

# In-memory buffer of latest traces for instant access, local testing, and offline fallback
_IN_MEMORY_TRACES: deque[ExecutionTrace] = deque(maxlen=MAX_IN_MEMORY_TRACES)


def _parse_json_field(value: Any, default: Any) -> Any:
    """Safely decode JSONB fields returned as strings or dicts from asyncpg."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _row_to_execution_trace(row: asyncpg.Record) -> ExecutionTrace:
    """Map an asyncpg execution_traces Record to an ExecutionTrace instance."""
    return ExecutionTrace(
        id=row["id"],
        thread_id=row["thread_id"],
        incident_id=row["incident_id"],
        fingerprint=row["fingerprint"],
        redacted_payload=_parse_json_field(row["redacted_payload"], {}),
        nodes_visited=_parse_json_field(row["nodes_visited"], []),
        mcp_tool_calls=_parse_json_field(row["mcp_tool_calls"], []),
        latency_ms=row["latency_ms"],
        token_cost=row["token_cost"],
        tokens_used=row["tokens_used"],
        mode=row.get("mode", ""),
        final_verdict=row["final_verdict"],
        created_at=row["created_at"],
    )


# ── Postgres Trace Storage & Retrieval ─────────────────────────────


async def ensure_traces_table() -> None:
    """Create the execution_traces table and indexes in Postgres if not present."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_traces (
                    id SERIAL PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    incident_id INT,
                    fingerprint TEXT NOT NULL,
                    redacted_payload JSONB NOT NULL DEFAULT '{}',
                    nodes_visited JSONB NOT NULL DEFAULT '[]',
                    mcp_tool_calls JSONB NOT NULL DEFAULT '[]',
                    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    token_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    tokens_used INT NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL DEFAULT '',
                    final_verdict TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_traces_thread ON execution_traces (thread_id);
                CREATE INDEX IF NOT EXISTS idx_traces_created ON execution_traces (created_at DESC);
                """
            )
            logger.info("Observability traces table ensured.")
    except DB_ERRORS as exc:
        logger.warning("Could not initialize execution_traces table in Postgres: %s", exc)


async def record_execution_trace(
    thread_id: str,
    incident_id: int | None,
    fingerprint: str,
    redacted_payload: dict[str, Any],
    nodes_visited: list[str],
    mcp_tool_calls: list[dict[str, Any]],
    latency_ms: float,
    token_cost: float,
    tokens_used: int,
    final_verdict: str,
    mode: str = "",
) -> ExecutionTrace:
    """Persist structured execution trace to in-memory buffer and Postgres."""
    now = datetime.now(UTC)
    trace = ExecutionTrace(
        id=len(_IN_MEMORY_TRACES) + 1,
        thread_id=thread_id,
        incident_id=incident_id,
        fingerprint=fingerprint,
        redacted_payload=redacted_payload,
        nodes_visited=nodes_visited,
        mcp_tool_calls=mcp_tool_calls,
        latency_ms=round(latency_ms, 2),
        token_cost=round(token_cost, 6),
        tokens_used=tokens_used,
        mode=mode,
        final_verdict=final_verdict,
        created_at=now,
    )
    _IN_MEMORY_TRACES.appendleft(trace)

    # Attempt Postgres persistence
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row_id: int = await conn.fetchval(
                """
                INSERT INTO execution_traces (
                    thread_id, incident_id, fingerprint, redacted_payload,
                    nodes_visited, mcp_tool_calls, latency_ms, token_cost,
                    tokens_used, mode, final_verdict, created_at
                )
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8, $9, $10, $11, $12)
                RETURNING id;
                """,
                thread_id,
                incident_id,
                fingerprint,
                json.dumps(redacted_payload),
                json.dumps(nodes_visited),
                json.dumps(mcp_tool_calls),
                latency_ms,
                token_cost,
                tokens_used,
                mode,
                final_verdict,
                now,
            )
            trace.id = row_id
    except DB_ERRORS as exc:
        logger.debug("Postgres unavailable for trace storage (%s), stored in-memory only", exc)

    return trace


async def update_execution_trace_verdict(
    thread_id: str,
    final_verdict: str,
    nodes_visited: list[str] | None = None,
    mcp_tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Update the final_verdict and optional telemetry for an existing execution trace."""
    # Update in-memory buffer
    for t in _IN_MEMORY_TRACES:
        if t.thread_id == thread_id:
            t.final_verdict = final_verdict
            if nodes_visited is not None:
                t.nodes_visited = nodes_visited
            if mcp_tool_calls is not None:
                t.mcp_tool_calls = mcp_tool_calls
            break

    # Update in Postgres
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if nodes_visited is not None and mcp_tool_calls is not None:
                await conn.execute(
                    """
                    UPDATE execution_traces
                    SET final_verdict = $1,
                        nodes_visited = $2::jsonb,
                        mcp_tool_calls = $3::jsonb
                    WHERE thread_id = $4;
                    """,
                    final_verdict,
                    json.dumps(nodes_visited),
                    json.dumps(mcp_tool_calls),
                    thread_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE execution_traces
                    SET final_verdict = $1
                    WHERE thread_id = $2;
                    """,
                    final_verdict,
                    thread_id,
                )
    except DB_ERRORS as exc:
        logger.debug("Failed updating trace verdict in Postgres (%s)", exc)


async def get_execution_trace_by_thread_id(thread_id: str) -> ExecutionTrace | None:
    """Retrieve a single execution trace by thread ID, falling back to in-memory buffer."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, thread_id, incident_id, fingerprint, redacted_payload,
                       nodes_visited, mcp_tool_calls, latency_ms, token_cost,
                       tokens_used, mode, final_verdict, created_at
                FROM execution_traces
                WHERE thread_id = $1
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                thread_id,
            )
            if row:
                return _row_to_execution_trace(row)
    except DB_ERRORS as exc:
        logger.debug("Failed reading trace %s from Postgres (%s), checking in-memory buffer", thread_id, exc)

    # In-memory fallback
    for trace in _IN_MEMORY_TRACES:
        if trace.thread_id == thread_id:
            return trace
    return None


async def get_latest_execution_traces(
    limit: int = DEFAULT_TRACE_LIMIT,
) -> list[ExecutionTrace]:
    """Retrieve recent traces from Postgres, falling back to memory buffer."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, thread_id, incident_id, fingerprint, redacted_payload,
                       nodes_visited, mcp_tool_calls, latency_ms, token_cost,
                       tokens_used, mode, final_verdict, created_at
                FROM execution_traces
                ORDER BY created_at DESC
                LIMIT $1;
                """,
                limit,
            )
            if rows:
                return [_row_to_execution_trace(r) for r in rows]
    except DB_ERRORS as exc:
        logger.debug("Failed reading traces from Postgres (%s), using in-memory buffer", exc)

    # In-memory fallback
    return list(_IN_MEMORY_TRACES)[:limit]
