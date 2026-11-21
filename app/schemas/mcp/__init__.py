"""Pydantic v2 schemas for MCP tools (trace, git, tracker)."""

from __future__ import annotations

from app.schemas.mcp.git import BlameEntry, CommitInfo, DiffOutput
from app.schemas.mcp.trace import ParsedStackTrace
from app.schemas.mcp.tracker import (
    IssueRecord,
    IssueStatus,
    PRPreview,
    PRStatus,
    SeverityLevel,
)

__all__ = [
    # trace_mcp
    "ParsedStackTrace",
    # git_mcp
    "BlameEntry",
    "CommitInfo",
    "DiffOutput",
    # tracker_mcp
    "IssueRecord",
    "IssueStatus",
    "PRPreview",
    "PRStatus",
    "SeverityLevel",
]
