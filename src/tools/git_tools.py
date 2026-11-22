"""Read-only git history tools for the history agent. Each tool takes one Pydantic args model."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.services.repo_cache import run_git

MAX_COMMITS = 10
MAX_BLAME_LINES = 40


class RecentCommitsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled argument is an error the LLM sees

    path: str = Field(description="File path relative to the repo root")
    limit: int = Field(5, ge=1, description="How many commits to list (max 10)")


class BlameLinesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled argument is an error the LLM sees

    path: str = Field(description="File path relative to the repo root")
    start: int = Field(ge=1, description="First line")
    end: int = Field(ge=1, description="Last line (max 40 lines per call)")


class ShowCommitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled argument is an error the LLM sees

    sha: str = Field(pattern=r"^[0-9a-fA-F]{4,40}$", description="Commit SHA (short or full)")
    path: str | None = Field(None, description="Only show the diff for this file")


class GitTools:
    """Tools bound to one checked-out repo. `--` before paths stops them being read as options."""

    def __init__(self, repo: Path):
        self.repo = repo.resolve()

    def recent_commits(self, args: RecentCommitsArgs) -> str:
        """Most recent commits that touched a file: short sha, date, author, subject."""
        limit = min(args.limit, MAX_COMMITS)
        output = run_git(self.repo, "log", f"-n{limit}", "--date=short", "--format=%h %ad %an: %s", "--", args.path)
        return output.strip() or "no commits touch this path"

    def blame_lines(self, args: BlameLinesArgs) -> str:
        """Which commit last changed each line in a range (max 40 lines)."""
        end = min(args.end, args.start + MAX_BLAME_LINES - 1)
        return run_git(self.repo, "blame", "--date=short", "-L", f"{args.start},{end}", "--", args.path)

    def show_commit(self, args: ShowCommitArgs) -> str:
        """A commit's message, changed-file stats and diff (optionally only for one file)."""
        paths = ["--", args.path] if args.path else []
        return run_git(self.repo, "show", "--stat", "--patch", "--format=%H%n%an %ad%n%n%B", args.sha, *paths)
