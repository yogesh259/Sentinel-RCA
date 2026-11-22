"""Pydantic v2 response schemas for the incident endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    """Response from POST /v1/incidents/ingest."""

    status: str
    fingerprint: str | None = None
    duplicate_count: int | None = None
    incident_id: int | str | None = None
    thread_id: str | None = None
    mode: str = ""
    llm_cost: str = "$0"


class IncidentRun(BaseModel):
    """Progress and verdict of one RCA run, read from its checkpoint."""

    thread_id: str
    repo: str
    phase: Literal["queued", "running", "awaiting_approval", "done"]
    nodes_visited: list[str] = Field(default_factory=list)
    exception_type: str = ""
    target_file: str = ""
    line_number: int = 0
    suspect_sha: str = ""
    confidence: str = ""
    severity: str = ""
    root_cause: str = ""
    mechanism: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    fix_suggestion: str = ""
    action: str = ""
    gate_reason: str = ""
    status: str = ""
    result: dict[str, Any] | None = None
    redacted_trace: str = ""
    guardrail_events: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
