"""Pydantic v2 schemas for MCP server inputs and outputs (Phase 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── trace_mcp schemas ─────────────────────────────────────────────


class ParsedStackTrace(BaseModel):
    """Structured result of parsing a raw Java/Python stack trace."""

    exception_type: str = Field(..., description="e.g. NullPointerException")
    exception_message: str = Field("", description="The error message")
    target_file: str = Field("", description="Java/Python source file name")
    class_name: str = Field("", description="Fully-qualified class or module")
    method_name: str = Field("", description="Method / function where the crash occurred")
    line_number: int = Field(0, description="Line number in the target file")
    redaction_types: list[str] = Field(
        default_factory=list,
        description="PII/secret redaction labels that were applied",
    )


# ── git_mcp schemas ───────────────────────────────────────────────


class CommitInfo(BaseModel):
    """Summary of a single git commit."""

    sha: str
    short_sha: str
    author: str
    date: str
    message: str


class DiffOutput(BaseModel):
    """Full diff for a single commit."""

    sha: str
    diff: str


class BlameEntry(BaseModel):
    """Git blame result for a specific line."""

    commit_sha: str
    author: str
    date: str
    line_number: int
    line_content: str


# ── tracker_mcp schemas ──────────────────────────────────────────


class IssueRecord(BaseModel):
    """An entry in the issues tracking table."""

    id: Optional[int] = None
    title: str
    exception_type: str
    file_name: str
    root_cause: str = ""
    commit_sha: str = ""
    severity: str = "medium"
    status: str = "open"
    created_at: Optional[datetime] = None


class PRPreview(BaseModel):
    """Structured preview of a proposed hotfix pull request."""

    title: str
    branch_name: str
    commit_sha: str
    file_path: str
    patch_summary: str
    description: str
    status: str = "draft"
