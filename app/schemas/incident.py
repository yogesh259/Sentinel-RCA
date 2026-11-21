"""Pydantic v2 schemas for incident payloads, records, and ingestion responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ── Generic incident payload (non-Sentry) ─────────────────────────


class IncidentEvent(BaseModel):
    """Generic incident payload: service + error + stack_trace."""

    service: str = Field("unknown-service", description="Originating service name")
    error: str = Field("", description="Error message or exception name")
    stack_trace: str = Field("", description="Raw stack trace string")

    @model_validator(mode="after")
    def validate_has_error_or_trace(self) -> IncidentEvent:
        if not self.error.strip() and not self.stack_trace.strip():
            raise ValueError("IncidentEvent must provide either 'error' or 'stack_trace'.")
        return self


# ── Domain Types & API Response Schemas ────────────────────────────

ExecutionMode = Literal["LIVE_GROQ", "SIMULATION", ""]
IncidentStatus = Literal["queued", "processing", "completed", "escalated", "rejected", "failed"]


class IngestResponse(BaseModel):
    """Response from POST /v1/incidents/ingest."""

    status: str
    fingerprint: str | None = None
    duplicate_count: int | None = None
    incident_id: int | str | None = None
    thread_id: str | None = None
    mode: ExecutionMode = ""
    llm_cost: str = "$0"


# Keep backward-compatible alias
WebhookResponse = IngestResponse


# ── Postgres incident record ──────────────────────────────────────


class IncidentRecord(BaseModel):
    """Represents a row in the incidents table."""

    id: int | None = None
    fingerprint: str
    exception_type: str
    file_name: str
    line_number: int
    title: str
    raw_payload: dict[str, Any]
    status: str = "queued"
    created_at: datetime | None = None
