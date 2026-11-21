"""Domain DTOs and Pydantic v2 schemas for Sentinel-RCA."""


from src.schemas.approval import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalItem,
    DecisionType,
    ReviewerRole,
)
from src.schemas.incident import (
    ExecutionMode,
    IncidentEvent,
    IncidentRecord,
    IncidentStatus,
    IngestResponse,
    WebhookResponse,
)
from src.schemas.mcp import (
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
from src.schemas.sentry import (
    SentryEventData,
    SentryException,
    SentryExceptionEntry,
    SentryExceptionPayload,
    SentryExceptionValue,
    SentryStacktrace,
    SentryStacktraceFrame,
    SentryWebhookPayload,
)
from src.schemas.trace import ExecutionTrace

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
    # Trace
    "ExecutionTrace",
]
