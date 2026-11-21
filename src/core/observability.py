"""Postgres-backed observability layer for Sentinel-RCA.

Structured Execution Trace:
  Persists per-incident run telemetry (redacted payload, nodes visited,
  MCP tool calls, latency_ms, token cost, mode, final verdict) in Postgres
  with an in-memory deque fallback for local dev / tests.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import UTC, datetime

from pydantic import BaseModel, Field

logger = logging.getLogger("sentinel-rca.observability")


# ── Structured Execution Trace Schema ──────────────────────────────


class ExecutionTrace(BaseModel):
    id: int | None = None
    thread_id: str
    incident_id: int | None = None
    fingerprint: str = ""
    redacted_payload: dict = Field(default_factory=dict)
    nodes_visited: list[str] = Field(default_factory=list)
    mcp_tool_calls: list[dict] = Field(default_factory=list)
    latency_ms: float = 0.0
    token_cost: float = 0.0
    tokens_used: int = 0
    mode: str = ""  # Active model or provider (e.g. "groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash")
    final_verdict: str = ""
    created_at: datetime | None = None


# In-memory buffer of latest 100 traces for instant access / offline mode
_IN_MEMORY_TRACES: deque[dict] = deque(maxlen=100)


# ── Postgres Trace Storage ────────────────────────────────────────


async def ensure_traces_table() -> None:
    """Create the execution_traces table in Postgres if it doesn't exist."""
    from src.services.postgres import get_pool

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
    except Exception as exc:
        logger.warning("Could not initialize execution_traces table in Postgres: %s", exc)


async def record_execution_trace(
    thread_id: str,
    incident_id: int | None,
    fingerprint: str,
    redacted_payload: dict,
    nodes_visited: list[str],
    mcp_tool_calls: list[dict],
    latency_ms: float,
    token_cost: float,
    tokens_used: int,
    final_verdict: str,
    mode: str = "",
) -> ExecutionTrace:
    """Persist structured execution trace to Postgres and in-memory buffer."""
    now = datetime.now(UTC)
    trace_dict = {
        "id": len(_IN_MEMORY_TRACES) + 1,
        "thread_id": thread_id,
        "incident_id": incident_id,
        "fingerprint": fingerprint,
        "redacted_payload": redacted_payload,
        "nodes_visited": nodes_visited,
        "mcp_tool_calls": mcp_tool_calls,
        "latency_ms": round(latency_ms, 2),
        "token_cost": round(token_cost, 6),
        "tokens_used": tokens_used,
        "mode": mode,
        "final_verdict": final_verdict,
        "created_at": now,
    }
    _IN_MEMORY_TRACES.appendleft(trace_dict)

    # Attempt Postgres persistence
    try:
        from src.services.postgres import get_pool

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
            trace_dict["id"] = row_id
    except Exception as exc:
        logger.debug("Postgres unavailable for trace storage (%s), stored in-memory only", exc)

    return ExecutionTrace(**trace_dict)


async def update_execution_trace_verdict(
    thread_id: str,
    final_verdict: str,
    nodes_visited: list[str] | None = None,
    mcp_tool_calls: list[dict] | None = None,
) -> None:
    """Update the final_verdict and optional telemetry for an existing execution trace."""
    # Update in-memory buffer
    for t in _IN_MEMORY_TRACES:
        if t.get("thread_id") == thread_id:
            t["final_verdict"] = final_verdict
            if nodes_visited is not None:
                t["nodes_visited"] = nodes_visited
            if mcp_tool_calls is not None:
                t["mcp_tool_calls"] = mcp_tool_calls
            break

    # Update in Postgres
    try:
        from src.services.postgres import get_pool

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
    except Exception as exc:
        logger.debug("Failed updating trace verdict in Postgres (%s)", exc)


async def get_latest_execution_traces(limit: int = 20) -> list[ExecutionTrace]:
    """Retrieve recent traces from Postgres, falling back to memory buffer."""
    try:
        from src.services.postgres import get_pool

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
                return [
                    ExecutionTrace(
                        id=r["id"],
                        thread_id=r["thread_id"],
                        incident_id=r["incident_id"],
                        fingerprint=r["fingerprint"],
                        redacted_payload=json.loads(r["redacted_payload"])
                        if isinstance(r["redacted_payload"], str)
                        else r["redacted_payload"],
                        nodes_visited=json.loads(r["nodes_visited"])
                        if isinstance(r["nodes_visited"], str)
                        else r["nodes_visited"],
                        mcp_tool_calls=json.loads(r["mcp_tool_calls"])
                        if isinstance(r["mcp_tool_calls"], str)
                        else r["mcp_tool_calls"],
                        latency_ms=r["latency_ms"],
                        token_cost=r["token_cost"],
                        tokens_used=r["tokens_used"],
                        mode=r.get("mode", ""),
                        final_verdict=r["final_verdict"],
                        created_at=r["created_at"],
                    )
                    for r in rows
                ]
    except Exception as exc:
        logger.debug("Failed reading traces from Postgres (%s), using in-memory buffer", exc)

    # In-memory fallback
    traces = list(_IN_MEMORY_TRACES)[:limit]
    return [ExecutionTrace(**t) for t in traces]
