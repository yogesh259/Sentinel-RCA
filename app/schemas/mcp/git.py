"""Pydantic v2 schemas for git_mcp server (Phase 2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    line_number: int = Field(0, ge=0)
    line_content: str
