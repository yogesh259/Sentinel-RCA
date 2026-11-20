"""Pydantic v2 schemas for Sentry webhook payloads and API responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── Inbound Sentry webhook payload ─────────────────────────────────


class SentryException(BaseModel):
    """Minimal representation of a Sentry exception entry."""

    type: str = Field(..., description="Exception class, e.g. NullPointerException")
    value: str = Field("", description="Exception message")
    module: str | None = Field(None, description="Module / package")


class SentryStacktraceFrame(BaseModel):
    """A single frame from the Sentry stacktrace."""

    filename: str = ""
    lineno: int = 0
    function: str = ""
    context_line: str | None = None


class SentryStacktrace(BaseModel):
    frames: list[SentryStacktraceFrame] = Field(default_factory=list)


class SentryExceptionEntry(BaseModel):
    type: str = Field(..., description="Exception class")
    value: str = Field("", description="Exception message")
    stacktrace: SentryStacktrace | None = None


class SentryEventData(BaseModel):
    """The 'data' sub-object inside a Sentry webhook delivery."""

    event_id: str = ""
    title: str = ""
    culprit: str | None = None
    timestamp: float | None = None
    exception: dict | None = None  # raw exception payload


class SentryWebhookPayload(BaseModel):
    """Top-level Sentry webhook payload."""

    action: str = "created"
    data: dict = Field(default_factory=dict)
    actor: dict | None = None


# ── API response schemas ───────────────────────────────────────────


class WebhookResponse(BaseModel):
    status: str
    fingerprint: str | None = None
    duplicate_count: int | None = None
    incident_id: str | None = None
    thread_id: str | None = None
    llm_cost: str = "$0"


# ── Postgres incident record ──────────────────────────────────────


class IncidentRecord(BaseModel):
    """Represents a row in the incidents table."""

    id: int | None = None
    fingerprint: str
    exception_type: str
    file_name: str
    line_number: int
    title: str
    raw_payload: dict
    status: str = "queued"
    created_at: datetime | None = None


# ── Human-in-the-Loop Approvals ───────────────────────────────────


class ApprovalItem(BaseModel):
    """A paused RCA thread awaiting human decision."""

    thread_id: str
    incident_id: int | None = None
    proposal: dict
    reason: str
    status: str = "awaiting_approval"


class ApprovalDecisionRequest(BaseModel):
    """Request payload for POST /v1/approvals/{thread_id}/decision."""

    decision: Literal["approve", "reject"]
    reviewer_role: Literal["staff_sre", "junior_dev"]


class ApprovalDecisionResponse(BaseModel):
    """Response payload for POST /v1/approvals/{thread_id}/decision."""

    status: str
    decision: str
    reviewer_role: str
    thread_id: str
    workflow_status: str | None = None
    result: dict | None = None
