"""Domain DTOs and Pydantic v2 schemas for Sentinel-RCA."""

from __future__ import annotations

from app.schemas.approval import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalItem,
    DecisionType,
    ReviewerRole,
)
from app.schemas.incident import (
    ExecutionMode,
    IncidentEvent,
    IncidentRecord,
    IncidentStatus,
    IngestResponse,
    WebhookResponse,
)
from app.schemas.mcp import (
    BlameEntry,
    CommitInfo,
    DiffOutput,
    IssueRecord,
    IssueStatus,
    ParsedStackTrace,
    PRPreview,
    PRStatus,
    SeverityLevel,
)
from app.schemas.sentry import (
    SentryEventData,
    SentryException,
    SentryExceptionEntry,
    SentryExceptionPayload,
    SentryExceptionValue,
    SentryStacktrace,
    SentryStacktraceFrame,
    SentryWebhookPayload,
)

__all__ = [
    # Approval
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalItem",
    "DecisionType",
    "ReviewerRole",
    # Incident
    "ExecutionMode",
    "IncidentEvent",
    "IncidentRecord",
    "IncidentStatus",
    "IngestResponse",
    "WebhookResponse",
    # MCP
    "BlameEntry",
    "CommitInfo",
    "DiffOutput",
    "IssueRecord",
    "IssueStatus",
    "ParsedStackTrace",
    "PRPreview",
    "PRStatus",
    "SeverityLevel",
    # Sentry
    "SentryEventData",
    "SentryException",
    "SentryExceptionEntry",
    "SentryExceptionPayload",
    "SentryExceptionValue",
    "SentryStacktrace",
    "SentryStacktraceFrame",
    "SentryWebhookPayload",
]
