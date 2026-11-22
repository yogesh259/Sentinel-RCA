"""Incident ingestion gateway: the only way an error enters the pipeline.

Cheap checks run first (HMAC, Redis dedup, rate limit) so forged requests and alert
storms are rejected before any database write or LLM spend.
"""

import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status

from src.core.config import config
from src.core.redaction import redact_dict
from src.db.session import session_scope
from src.graph import get_graph, thread_config
from src.repositories.incidents import add_incident
from src.schemas.incident import IncidentRun, IngestResponse
from src.services.payload_adapter import IncidentSummary, summarise_incident
from src.services.redis_dedup import check_and_set_dedup, check_rate_limit, compute_fingerprint
from src.services.webhook_auth import verify_signature
from src.services.workflow_runner import run_rca_workflow

logger = logging.getLogger("sentinel-rca.api.incidents")

router = APIRouter(tags=["incidents"])


@router.post("/v1/incidents/ingest", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
@router.post("/v1/webhooks/sentry", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_incident(
    request: Request,
    background_tasks: BackgroundTasks,
    response: Response,
    x_webhook_signature: str | None = Header(None),
) -> IngestResponse:
    """Verify, deduplicate and rate-limit a webhook, then start its RCA run in the background."""
    body = await request.body()
    if not verify_signature(body, x_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Redact before anything is parsed, logged or stored.
    payload, _ = redact_dict(json.loads(body))
    incident = await summarise_incident(payload)
    fingerprint = compute_fingerprint(incident.exception_type, incident.file_name, incident.line_number)

    is_new, dup_count = await check_and_set_dedup(fingerprint)
    if not is_new:
        logger.info("Deduplicated event (fingerprint=%s, dup_count=%d)", fingerprint[:12], dup_count)
        response.status_code = status.HTTP_200_OK
        return IngestResponse(status="deduplicated", fingerprint=fingerprint, duplicate_count=dup_count)

    client_ip = request.client.host if request.client else "127.0.0.1"
    if not await is_within_rate_limit(client_ip):
        logger.warning("Rate limit exceeded for client IP %s; returning HTTP 429", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Too many incident ingestion requests; please retry later.",
        )

    incident_id = await persist_incident(fingerprint, incident, payload)
    # Unique per run: a recurring fingerprint after the dedup TTL must not resume an old thread.
    thread_id = f"thread_{fingerprint[:12]}_{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(
        run_rca_workflow,
        thread_id=thread_id,
        incident_id=incident_id,
        fingerprint=fingerprint,
        redacted_payload=payload,
    )

    return IngestResponse(
        status="queued",
        fingerprint=fingerprint,
        incident_id=str(incident_id) if incident_id else None,
        thread_id=thread_id,
        mode=config.primary_llm_model,
    )


async def is_within_rate_limit(client_ip: str) -> bool:
    """Fail open if Redis errors: losing an incident is worse than one extra LLM run."""
    try:
        return await check_rate_limit(client_ip)
    except Exception as exc:
        logger.warning("Rate limit check failed (%s); defaulting to allowed", exc)
        return True


async def persist_incident(fingerprint: str, incident: IncidentSummary, payload: dict) -> int | None:
    """Store the incident row; the run still proceeds without an id if Postgres is down."""
    try:
        async with session_scope() as session:
            row = await add_incident(
                session,
                fingerprint=fingerprint,
                exception_type=incident.exception_type,
                file_name=incident.file_name,
                line_number=incident.line_number,
                title=incident.title,
                redacted_payload=payload,
            )
    except Exception:
        logger.warning("Postgres insert failed — incident queued in-memory only.")
        return None
    logger.info("Queued incident #%d (fingerprint=%s)", row.id, fingerprint[:12])
    return row.id


@router.get("/v1/incidents/{thread_id}", response_model=IncidentRun)
async def get_incident_run(thread_id: str) -> IncidentRun:
    """Current progress and verdict of a run, straight from its LangGraph checkpoint."""
    snapshot = await get_graph().aget_state(thread_config(thread_id))
    state = snapshot.values or {}
    if not state:
        phase = "queued"
    elif snapshot.interrupts:
        phase = "awaiting_approval"
    elif snapshot.next:
        phase = "running"
    else:
        phase = "done"

    # While paused, the gate's proposal and reason live only in the interrupt payload.
    pending = snapshot.interrupts[0].value if snapshot.interrupts else {}
    proposal = pending.get("proposal") or state.get("proposal") or {}
    llm_calls = state.get("llm_calls") or []
    return IncidentRun(
        thread_id=thread_id,
        repo=config.github_repo,
        phase=phase,
        nodes_visited=state.get("nodes_visited") or [],
        exception_type=state.get("exception_type", ""),
        target_file=state.get("target_file", ""),
        line_number=state.get("line_number", 0),
        suspect_sha=state.get("suspect_sha", ""),
        confidence=state.get("confidence", ""),
        severity=str(state.get("suggested_severity", "")),
        root_cause=state.get("root_cause", ""),
        mechanism=state.get("mechanism", ""),
        evidence=state.get("evidence") or [],
        fix_suggestion=state.get("patch_summary", ""),
        action=proposal.get("action", ""),
        gate_reason=pending.get("reason") or state.get("auth_reason", ""),
        status=str(state.get("status", "")),
        result=state.get("result"),
        redacted_trace=state.get("raw_trace", ""),
        guardrail_events=state.get("guardrail_events") or [],
        tokens_used=sum(c["input_tokens"] + c["output_tokens"] for c in llm_calls),
        cost_usd=sum(c["cost_usd"] for c in llm_calls),
    )
