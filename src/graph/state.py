"""RCAState and domain context definitions for Sentinel-RCA LangGraph."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.graph.authorisation import Action, AuthDecision
from src.schemas.mcp.tracker import SeverityLevel


class WorkflowStatus(StrEnum):
    """Workflow execution statuses (Python 3.11+ StrEnum)."""

    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    ERROR = "error"


class IncidentContext(BaseModel):
    """Initial incident inputs ingested from the webhook or gateway."""

    model_config = ConfigDict(extra="allow")

    thread_id: str = ""
    incident_id: int | None = None
    fingerprint: str = ""
    raw_trace: str = ""
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ParsedTrace(BaseModel):
    """Trace details extracted during stack trace parsing."""

    model_config = ConfigDict(extra="allow")

    exception_type: str = ""
    exception_message: str = ""
    target_file: str = ""
    class_name: str = ""
    method_name: str = ""
    line_number: int = 0
    redaction_types: list[str] = Field(default_factory=list)


class DedupResult(BaseModel):
    """Deduplication and prior alert search outcome."""

    model_config = ConfigDict(extra="allow")

    is_duplicate: bool = False
    existing_issues: list[dict[str, Any]] = Field(default_factory=list)
    dedup_summary: str = ""
    triage_summary: str = ""


class GitInvestigation(BaseModel):
    """Git commit analysis and root-cause correlation findings."""

    model_config = ConfigDict(extra="allow")

    suspect_commits: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: str = ""
    suggested_severity: SeverityLevel | str = "medium"
    patch_summary: str = ""


class AuthorisationContext(BaseModel):
    """Policy engine proposal, authorization gate decision, and HITL state."""

    model_config = ConfigDict(extra="allow")

    force_action: Action | None = None
    proposal: dict[str, Any] = Field(default_factory=dict)
    auth_decision: AuthDecision | str = ""
    auth_reason: str = ""
    hitl_required: bool = False
    hitl_response: dict[str, Any] | None = None


class ExecutionSummary(BaseModel):
    """Terminal workflow execution metrics and dispatch results."""

    model_config = ConfigDict(extra="allow")

    status: WorkflowStatus | str = ""
    result: dict[str, Any] | None = None
    step_count: int = 0
    cost: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class RCAState(
    IncidentContext,
    ParsedTrace,
    DedupResult,
    GitInvestigation,
    AuthorisationContext,
    ExecutionSummary,
):
    """Unified LangGraph state composing all stage-specific domain models."""

    model_config = ConfigDict(extra="allow")

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like .get() compatibility for LangGraph nodes and callers."""
        val = getattr(self, key, default)
        return default if val is None and default is not None else val

    def __getitem__(self, key: str) -> Any:
        """Dict-like subscript compatibility."""
        return getattr(self, key)


__all__ = [
    "AuthorisationContext",
    "DedupResult",
    "ExecutionSummary",
    "GitInvestigation",
    "IncidentContext",
    "ParsedTrace",
    "RCAState",
    "WorkflowStatus",
]
