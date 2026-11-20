"""Pydantic v2 schemas for Sentry webhook payloads and API responses."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Inbound Sentry webhook payload ─────────────────────────────────


class SentryException(BaseModel):
    """Minimal representation of a Sentry exception entry."""

    type: str = Field(..., description="Exception class, e.g. NullPointerException")
    value: str = Field("", description="Exception message")
    module: Optional[str] = Field(None, description="Module / package")


class SentryStacktraceFrame(BaseModel):
    """A single frame from the Sentry stacktrace."""

    filename: str = ""
    lineno: int = 0
    function: str = ""
    context_line: Optional[str] = None


class SentryStacktrace(BaseModel):
    frames: list[SentryStacktraceFrame] = Field(default_factory=list)


class SentryExceptionEntry(BaseModel):
    type: str = Field(..., description="Exception class")
    value: str = Field("", description="Exception message")
    stacktrace: Optional[SentryStacktrace] = None


class SentryEventData(BaseModel):
    """The 'data' sub-object inside a Sentry webhook delivery."""

    event_id: str = ""
    title: str = ""
    culprit: Optional[str] = None
    timestamp: Optional[float] = None
    exception: Optional[dict] = None  # raw exception payload


class SentryWebhookPayload(BaseModel):
    """Top-level Sentry webhook payload."""

    action: str = "created"
    data: dict = Field(default_factory=dict)
    actor: Optional[dict] = None


# ── API response schemas ───────────────────────────────────────────


class WebhookResponse(BaseModel):
    status: str
    fingerprint: Optional[str] = None
    duplicate_count: Optional[int] = None
    incident_id: Optional[str] = None
    llm_cost: str = "$0"


# ── Postgres incident record ──────────────────────────────────────


class IncidentRecord(BaseModel):
    """Represents a row in the incidents table."""

    id: Optional[int] = None
    fingerprint: str
    exception_type: str
    file_name: str
    line_number: int
    title: str
    raw_payload: dict
    status: str = "queued"
    created_at: Optional[datetime] = None
