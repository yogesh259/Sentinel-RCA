"""Sentinel-RCA — FastAPI application entry-point.

API Endpoints:
  1. POST /v1/incidents/ingest — HMAC-SHA256 authenticated webhook gateway with
     Redis storm suppression, rate limiting, and 202 Accepted async dispatch.
  2. GET /v1/approvals — List paused LangGraph threads awaiting HITL review.
  3. POST /v1/approvals/{thread_id}/decision — Enforce RBAC and resume thread.
  4. GET /v1/traces — Structured execution audit logs for Ops console.
  5. GET /health — Deep health check pinging Neon Postgres and Upstash Redis.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response, status
from langgraph.types import Command

from app.core.config import settings
from app.core.observability import (
    ExecutionTrace,
    ensure_traces_table,
    get_latest_execution_traces,
    record_execution_trace,
    update_execution_trace_verdict,
)
from app.core.redaction import redact_dict
from app.graph.authorisation import check_rbac
from app.graph.workflow import (
    get_all_pending_approvals,
    get_graph,
    get_pending_approval,
    remove_pending_approval,
)
from app.mcp_servers.tracker_mcp import ensure_issues_table
from app.schemas.approval import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalItem,
)
from app.schemas.incident import IngestResponse
from app.services.postgres import close_pool, ensure_tables, get_pool, insert_incident
from app.services.redis_dedup import (
    check_and_set_dedup,
    check_rate_limit,
    close_redis,
    compute_fingerprint,
    get_redis,
)
from app.services.webhook_auth import verify_signature

logger = logging.getLogger("sentinel-rca")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ── Background Worker ──────────────────────────────────────────────


async def run_rca_workflow(
    thread_id: str,
    incident_id: int | None,
    fingerprint: str,
    payload: dict,
    mode: str = "SIMULATION",
) -> None:
    """Execute the LangGraph RCA workflow in the background and record telemetry."""
    logger.info(
        "Executing RCA workflow in background: thread_id=%s incident_id=%s mode=%s",
        thread_id,
        incident_id,
        mode,
    )
    t0 = time.perf_counter()
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    sanitized_payload, _p_types = redact_dict(payload)
    initial_state = {
        "thread_id": thread_id,
        "incident_id": incident_id,
        "fingerprint": fingerprint,
        "raw_payload": sanitized_payload,
    }
    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "Workflow finished for %s: status=%s in %.2fms",
            thread_id,
            final_state.get("status"),
            latency_ms,
        )

        cost_info = final_state.get("cost", {})
        tokens_used = cost_info.get("tokens_used", 0)
        cost_usd = cost_info.get("cost_usd", 0.0)

        nodes_visited = [
            "parse_trace",
            "semantic_dedup",
            "investigate_git",
            "authorisation_gate",
        ]
        if final_state.get("status") in ("completed", "escalated", "rejected"):
            nodes_visited.append("execute_action")

        tool_calls: list[dict] = []
        if final_state.get("suspect_commits"):
            tool_calls.append(
                {
                    "tool": "git_mcp.get_recent_commits",
                    "file": final_state.get("target_file"),
                }
            )
            tool_calls.append(
                {
                    "tool": "git_mcp.get_commit_diff",
                    "sha": final_state["suspect_commits"][0].get("sha"),
                }
            )
        if final_state.get("result"):
            action = final_state.get("proposal", {}).get("action", "unknown")
            tool_calls.append({"tool": f"tracker_mcp.{action}", "result": final_state["result"]})

        is_interrupted = bool(final_state.get("__interrupt__")) or (
            get_pending_approval(thread_id) is not None
        )
        if is_interrupted:
            verdict = "AWAITING_APPROVAL"
        else:
            verdict = final_state.get("status") or "completed"

        await record_execution_trace(
            thread_id=thread_id,
            incident_id=incident_id,
            fingerprint=fingerprint,
            redacted_payload=sanitized_payload,
            nodes_visited=nodes_visited,
            mcp_tool_calls=tool_calls,
            latency_ms=latency_ms,
            token_cost=cost_usd,
            tokens_used=tokens_used,
            mode=mode,
            final_verdict=verdict,
        )
    except Exception as exc:
        logger.error("RCA background workflow failed for %s: %s", thread_id, exc, exc_info=True)


# ── Lifespan ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks."""
    logger.info("Starting Sentinel-RCA …")
    try:
        await ensure_tables()
        await ensure_issues_table()
        await ensure_traces_table()
        logger.info("Postgres tables ensured.")
    except Exception as exc:
        logger.warning("Postgres init failed (%s) — running without DB persistence.", exc)
    yield
    await close_redis()
    await close_pool()
    logger.info("Sentinel-RCA shut down.")


app = FastAPI(
    title="Sentinel-RCA",
    version="0.5.0",
    description="Self-hosted Error RCA engine with MCP, LangGraph, and deterministic RBAC",
    lifespan=lifespan,
)


# ── Payload Adapter Helper ─────────────────────────────────────────


def _extract_exception_info(payload: dict) -> tuple[str, str, int, str]:
    """Pull (exception_type, file_name, line_number, title) from Sentry or generic payload."""
    exception_type = "UnknownException"
    file_name = "unknown"
    line_number = 0

    # 1. Generic payload format: {service, error, stack_trace}
    if "stack_trace" in payload or "service" in payload or "error" in payload:
        service = payload.get("service", "service")
        error = payload.get("error", "Error")
        raw_trace = payload.get("stack_trace", "")

        if raw_trace:
            from app.mcp_servers.trace_mcp import parse_stack_trace

            parsed = parse_stack_trace(raw_trace)
            exception_type = parsed.exception_type or error
            file_name = parsed.target_file or "unknown"
            line_number = parsed.line_number
        else:
            exception_type = error

        title = f"{service}: {error}" if service else error
        return exception_type, file_name, line_number, title

    # 2. Sentry webhook format
    raw_data = payload.get("data", {})
    title = raw_data.get("title", raw_data.get("event", {}).get("title", ""))

    event = payload.get("data", {}).get("event", payload.get("data", {}))
    exc_info = event.get("exception", {})
    values = exc_info.get("values", [])

    if values:
        top = values[0]
        exception_type = top.get("type", exception_type)
        stacktrace = top.get("stacktrace", {})
        frames = stacktrace.get("frames", [])
        if frames:
            last_frame = frames[-1]
            file_name = last_frame.get("filename", file_name)
            line_number = last_frame.get("lineno", line_number)

    if not title:
        title = f"{exception_type} in {file_name}:{line_number}"

    return exception_type, file_name, line_number, title


# ── Ingestion Endpoint ─────────────────────────────────────────────


@app.post("/v1/incidents/ingest", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
@app.post("/v1/webhooks/sentry", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_incident(
    request: Request,
    background_tasks: BackgroundTasks,
    response: Response,
    x_webhook_signature: str | None = Header(None),
) -> IngestResponse:
    """Ingest incident, verify HMAC, deduplicate via Redis, and enqueue workflow."""
    body = await request.body()

    # Step 1: HMAC-SHA256 verification
    if not verify_signature(body, x_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    raw_payload: dict = json.loads(body)
    redacted_payload, _applied_redactions = redact_dict(raw_payload)

    # Step 2: Extract exception metadata (normalizes Sentry + generic payloads)
    exception_type, file_name, line_number, title = _extract_exception_info(redacted_payload)

    # Step 3: Deterministic fingerprint
    fingerprint = compute_fingerprint(exception_type, file_name, line_number)

    # Step 4: Redis SETNX storm suppression
    is_new, dup_count = await check_and_set_dedup(fingerprint)

    if not is_new:
        logger.info(
            "Deduplicated event (fingerprint=%s, dup_count=%d)", fingerprint[:12], dup_count
        )
        response.status_code = status.HTTP_200_OK
        return IngestResponse(
            status="deduplicated",
            fingerprint=fingerprint,
            duplicate_count=dup_count,
            llm_cost="$0",
        )

    # Step 5: Redis rate limit check — reject immediately on rate-limit exhaustion
    client_ip = request.client.host if request.client else "127.0.0.1"
    try:
        is_rate_allowed = await check_rate_limit(client_ip)
    except Exception as exc:
        logger.warning("Rate limit check failed (%s); defaulting to allowed", exc)
        is_rate_allowed = True

    if not is_rate_allowed:
        logger.warning("Rate limit exceeded for client IP %s; returning HTTP 429", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Too many incident ingestion requests; please retry later.",
        )

    groq_key = (
        settings.groq_api_key.get_secret_value()
        if hasattr(settings.groq_api_key, "get_secret_value")
        else str(settings.groq_api_key)
    )
    mode = "LIVE_GROQ" if groq_key else "LIVE_GROQ"

    # Step 6: Persist to Postgres
    incident_id: int | None = None
    try:
        incident_id = await insert_incident(
            fingerprint=fingerprint,
            exception_type=exception_type,
            file_name=file_name,
            line_number=line_number,
            title=title,
            raw_payload=redacted_payload,
        )
        logger.info("Queued incident #%d (fingerprint=%s)", incident_id, fingerprint[:12])
    except Exception:
        logger.warning("Postgres insert failed — incident queued in-memory only.")

    thread_id = f"thread_{fingerprint[:16]}"

    # Step 7: Dispatch LangGraph workflow via BackgroundTasks
    background_tasks.add_task(
        run_rca_workflow,
        thread_id=thread_id,
        incident_id=incident_id,
        fingerprint=fingerprint,
        payload=redacted_payload,
        mode=mode,
    )

    response.status_code = status.HTTP_202_ACCEPTED
    return IngestResponse(
        status="queued",
        fingerprint=fingerprint,
        incident_id=str(incident_id) if incident_id else None,
        thread_id=thread_id,
        mode=mode,
        llm_cost="$0",
    )


# ── Human-in-the-Loop Approvals Endpoints ──────────────────────────


@app.get("/v1/approvals", response_model=list[ApprovalItem])
async def list_pending_approvals() -> list[ApprovalItem]:
    """List all paused incidents currently awaiting human review."""
    items = get_all_pending_approvals()
    return [
        ApprovalItem(
            thread_id=item["thread_id"],
            incident_id=item.get("incident_id"),
            proposal=item.get("proposal", {}),
            reason=item.get("reason", ""),
            status=item.get("status", "awaiting_approval"),
        )
        for item in items
    ]


@app.post("/v1/approvals/{thread_id}/decision", response_model=ApprovalDecisionResponse)
async def submit_approval_decision(
    thread_id: str,
    body: ApprovalDecisionRequest,
) -> ApprovalDecisionResponse:
    """Submit a human approval/rejection decision and resume the workflow.

    Enforces deterministic RBAC:
      - Only 'staff_sre' may approve 'draft_hotfix_pr'.
      - Other roles are rejected with 403 Forbidden.
    """
    pending = get_pending_approval(thread_id)
    if not pending:
        raise HTTPException(
            status_code=404,
            detail=f"Approval thread '{thread_id}' not found or already resolved.",
        )

    proposal = pending.get("proposal", {})
    action = proposal.get("action", "")

    # RBAC gate
    allowed, rbac_reason = check_rbac(action, body.reviewer_role)
    if not allowed and body.decision == "approve":
        raise HTTPException(status_code=403, detail=rbac_reason)

    # Resume LangGraph thread
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    command = Command(resume={"decision": body.decision, "reviewer_role": body.reviewer_role})

    try:
        resumed_state = await graph.ainvoke(command, config=config)
    except Exception as exc:
        logger.error("Failed to resume thread %s: %s", thread_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Error resuming workflow thread: {exc}",
        ) from exc

    remove_pending_approval(thread_id)

    # Update trace verdict in Postgres & in-memory buffer
    updated_verdict = "REJECTED" if body.decision == "reject" else "APPROVED"

    try:
        extra_tools: list[dict] = []
        if resumed_state.get("result"):
            res_action = resumed_state.get("proposal", {}).get("action", action)
            extra_tools.append(
                {"tool": f"tracker_mcp.{res_action}", "result": resumed_state["result"]}
            )

        traces = await get_latest_execution_traces(limit=50)
        existing_trace = next((t for t in traces if t.thread_id == thread_id), None)
        nodes = (
            list(existing_trace.nodes_visited)
            if existing_trace
            else ["parse_trace", "semantic_dedup", "investigate_git", "authorisation_gate"]
        )
        if "execute_action" not in nodes and body.decision == "approve":
            nodes.append("execute_action")

        tools = list(existing_trace.mcp_tool_calls) if existing_trace else []
        for et in extra_tools:
            if et not in tools:
                tools.append(et)

        await update_execution_trace_verdict(
            thread_id=thread_id,
            final_verdict=updated_verdict,
            nodes_visited=nodes,
            mcp_tool_calls=tools,
        )
    except Exception as exc:
        logger.warning("Could not update trace verdict for thread %s: %s", thread_id, exc)

    return ApprovalDecisionResponse(
        status="resumed",
        decision=body.decision,
        reviewer_role=body.reviewer_role,
        thread_id=thread_id,
        workflow_status=resumed_state.get("status"),
        result=resumed_state.get("result"),
    )


# ── Observability & Health Endpoints ───────────────────────────────


@app.get("/v1/traces", response_model=list[ExecutionTrace])
async def list_execution_traces(limit: int = 20) -> list[ExecutionTrace]:
    """Retrieve structured execution traces for live UI console."""
    return await get_latest_execution_traces(limit=limit)


@app.get("/health")
async def health_check() -> dict:
    """Deep health check verifying Neon Postgres and Upstash Redis connectivity."""
    pg_ok = False
    redis_ok = False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1;")
            pg_ok = val == 1
    except Exception as exc:
        logger.warning("Health check Postgres ping failed: %s", exc)

    try:
        r = await get_redis()
        pong = await r.ping()
        redis_ok = bool(pong)
    except Exception as exc:
        logger.warning("Health check Redis ping failed: %s", exc)

    if pg_ok and redis_ok:
        return {"status": "ok", "postgres": "connected", "redis": "connected"}

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "status": "degraded",
            "postgres": "connected" if pg_ok else "unavailable",
            "redis": "connected" if redis_ok else "unavailable",
        },
    )
