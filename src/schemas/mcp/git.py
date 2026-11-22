"""Pydantic v2 schemas for git_mcp server."""


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


class BlameRange(BaseModel):
    """A contiguous block of lines last modified by one commit (real `git blame`)."""

    commit_sha: str
    short_sha: str
    author: str
    date: str
    message: str
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
