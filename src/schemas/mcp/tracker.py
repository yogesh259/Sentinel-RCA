"""Pydantic v2 schemas for tracker_mcp server (Phase 2)."""


from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, field_validator

SeverityLevel = Literal["low", "medium", "high", "critical"]
IssueStatus = Literal["open", "in_progress", "resolved", "closed"]
PRStatus = Literal["draft", "pending_approval", "approved", "rejected", "merged"]


class IssueRecord(BaseModel):
    """An entry in the issues tracking table."""

    id: int | None = None
    title: str
    exception_type: str
    file_name: str
    root_cause: str = ""
    commit_sha: str = ""
    severity: SeverityLevel = "medium"
    status: IssueStatus = "open"
    created_at: datetime | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.lower()
        return v


class PRPreview(BaseModel):
    """Structured preview of a proposed hotfix pull request."""

    title: str
    branch_name: str
    commit_sha: str
    file_path: str
    patch_summary: str
    description: str
    status: PRStatus = "draft"
