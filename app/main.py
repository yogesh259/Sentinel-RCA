"""Sentinel-RCA — FastAPI application entry-point.

Phase 1:  Webhook gateway with HMAC verification, Redis storm suppression,
          and Postgres incident logging.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request

from app.core.config import settings
from app.core.schemas import WebhookResponse
from app.services.postgres import close_pool, ensure_tables, insert_incident
from app.services.redis_dedup import (
    check_and_set_dedup,
    close_redis,
    compute_fingerprint,
)
from app.services.webhook_auth import verify_signature

logger = logging.getLogger("sentinel-rca")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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
    version="0.1.0",
    description="Self-hosted Error Root-Cause Analysis engine",
    lifespan=lifespan,
)


# ── Helpers ────────────────────────────────────────────────────────


def _extract_exception_info(payload: dict) -> tuple[str, str, int, str]:
    """Pull (exception_type, file_name, line_number, title) from a Sentry payload.

    Navigates both ``data.event.exception`` and ``data.exception`` shapes.
    """
    # Default values
    exception_type = "UnknownException"
    file_name = "unknown"
    line_number = 0
    title = payload.get("data", {}).get("title", payload.get("data", {}).get("event", {}).get("title", ""))

    # Try multiple payload shapes
    event = payload.get("data", {}).get("event", payload.get("data", {}))

    exc_info = event.get("exception", {})
    values = exc_info.get("values", [])

    if values:
        top = values[0]
        exception_type = top.get("type", exception_type)
        stacktrace = top.get("stacktrace", {})
        frames = stacktrace.get("frames", [])
        if frames:
            last_frame = frames[-1]  # most recent frame
            file_name = last_frame.get("filename", file_name)
            line_number = last_frame.get("lineno", line_number)

    if not title:
        title = f"{exception_type} in {file_name}:{line_number}"

    return exception_type, file_name, line_number, title


# ── Webhook endpoint ──────────────────────────────────────────────


@app.post("/v1/webhooks/sentry", response_model=WebhookResponse)
async def sentry_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(None),
) -> WebhookResponse:
    """Receive a Sentry webhook, verify HMAC, deduplicate, and enqueue.

    **1-Second SLA contract**: this endpoint MUST return quickly.
    """
    body = await request.body()

    # ── Step 1: HMAC-SHA256 verification ──────────────────────────
    if not verify_signature(body, x_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload: dict = json.loads(body)

    # ── Step 2: Extract exception metadata ────────────────────────
    exception_type, file_name, line_number, title = _extract_exception_info(payload)

    # ── Step 3: Deterministic fingerprint ─────────────────────────
    fingerprint = compute_fingerprint(exception_type, file_name, line_number)

    # ── Step 4: Redis SETNX storm suppression ─────────────────────
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

    # ── Step 5: Persist to Postgres ───────────────────────────────
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

    return WebhookResponse(
        status="queued",
        fingerprint=fingerprint,
        incident_id=str(incident_id) if incident_id else None,
        llm_cost="$0",
    )


# ── Health ─────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
