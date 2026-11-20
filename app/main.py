"""Sentinel-RCA — FastAPI application entry-point.

Phase 1: Webhook gateway with HMAC verification & Redis storm suppression.
Phase 2: Redaction middleware & isolated MCP tools.
Phase 3: LangGraph stateful coordinator, BackgroundTask worker & RBAC approval gate.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from langgraph.types import Command

from app.core.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalItem,
    WebhookResponse,
)
from app.graph.authorisation import check_rbac
from app.graph.workflow import (
    get_all_pending_approvals,
    get_graph,
    get_pending_approval,
    remove_pending_approval,
)
from app.services.postgres import close_pool, ensure_tables, insert_incident
from app.services.redis_dedup import (
    check_and_set_dedup,
    close_redis,
    compute_fingerprint,
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
) -> None:
    """Execute the LangGraph RCA workflow in the background."""
    logger.info(
        "Executing RCA workflow in background: thread_id=%s incident_id=%s",
        thread_id,
        incident_id,
    )
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "thread_id": thread_id,
        "incident_id": incident_id,
        "fingerprint": fingerprint,
        "raw_payload": payload,
    }
    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        logger.info(
            "Workflow finished for %s: status=%s",
            thread_id,
            final_state.get("status"),
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
        logger.info("Postgres tables ensured.")
    except Exception:
        logger.warning("Postgres not available — running without DB persistence.")
    yield
    await close_redis()
    await close_pool()
    logger.info("Sentinel-RCA shut down.")


app = FastAPI(
    title="Sentinel-RCA",
    version="0.3.0",
    description="Self-hosted Error RCA engine with MCP, LangGraph, and deterministic RBAC",
    lifespan=lifespan,
)


# ── Helpers ────────────────────────────────────────────────────────


def _extract_exception_info(payload: dict) -> tuple[str, str, int, str]:
    """Pull (exception_type, file_name, line_number, title) from a Sentry payload."""
    exception_type = "UnknownException"
    file_name = "unknown"
    line_number = 0
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


# ── Webhook endpoint ──────────────────────────────────────────────


@app.post("/v1/webhooks/sentry", response_model=WebhookResponse)
async def sentry_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_signature: str | None = Header(None),
) -> WebhookResponse:
    """Receive a Sentry webhook, verify HMAC, deduplicate, and enqueue.

    **1-Second SLA contract**: this endpoint MUST return immediately.
    """
    body = await request.body()

    # Step 1: HMAC-SHA256 verification
    if not verify_signature(body, x_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload: dict = json.loads(body)

    # Step 2: Extract exception metadata
    exception_type, file_name, line_number, title = _extract_exception_info(payload)

    # Step 3: Deterministic fingerprint
    fingerprint = compute_fingerprint(exception_type, file_name, line_number)

    # Step 4: Redis SETNX storm suppression
    is_new, dup_count = await check_and_set_dedup(fingerprint)

    if not is_new:
        logger.info(
            "Deduplicated event (fingerprint=%s, dup_count=%d)", fingerprint[:12], dup_count
        )
        return WebhookResponse(
            status="deduplicated",
            fingerprint=fingerprint,
            duplicate_count=dup_count,
            llm_cost="$0",
        )

    # Step 5: Persist to Postgres
    incident_id: int | None = None
    try:
        incident_id = await insert_incident(
            fingerprint=fingerprint,
            exception_type=exception_type,
            file_name=file_name,
            line_number=line_number,
            title=title,
            raw_payload=payload,
        )
        logger.info("Queued incident #%d (fingerprint=%s)", incident_id, fingerprint[:12])
    except Exception:
        logger.warning("Postgres insert failed — incident queued in-memory only.")

    thread_id = f"thread_{fingerprint[:16]}"

    # Step 6: Dispatch LangGraph workflow via BackgroundTasks
    background_tasks.add_task(
        run_rca_workflow,
        thread_id=thread_id,
        incident_id=incident_id,
        fingerprint=fingerprint,
        payload=payload,
    )

    return WebhookResponse(
        status="queued",
        fingerprint=fingerprint,
        incident_id=str(incident_id) if incident_id else None,
        thread_id=thread_id,
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

    return ApprovalDecisionResponse(
        status="resumed",
        decision=body.decision,
        reviewer_role=body.reviewer_role,
        thread_id=thread_id,
        workflow_status=resumed_state.get("status"),
        result=resumed_state.get("result"),
    )


# ── Health ─────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
