"""git_mcp — Standalone MCP server for read-only git operations.

Runs real git commands against the ./victim-service repository.
All inputs are **strictly validated** to prevent shell injection.

Exposes:
    get_recent_commits(file_path, limit) -> list[CommitInfo]
    get_commit_diff(commit_sha)          -> DiffOutput
    get_git_blame(file_path, line_number) -> BlameEntry
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.core.mcp_schemas import BlameEntry, CommitInfo, DiffOutput

# ── Configuration ─────────────────────────────────────────────────

# Default repo path — resolved relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_REPO_PATH = _PROJECT_ROOT / "victim-service"

# ── Input validation ──────────────────────────────────────────────

# Allow only safe characters in file paths: alphanumerics, /, -, _, .
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9/_.\-]+$")

# SHA: 4-40 hex chars
_SAFE_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


class GitInputValidationError(ValueError):
    """Raised when a git_mcp input contains unsafe characters."""

    pass


def _validate_file_path(file_path: str) -> str:
    """Validate and normalise a file path — reject shell-injection characters."""
    if not file_path or not file_path.strip():
        raise GitInputValidationError("file_path must not be empty")

    cleaned = file_path.strip()

    # Block path traversal
    if ".." in cleaned:
        raise GitInputValidationError(
            f"file_path contains path-traversal sequence '..': {cleaned!r}"
        )

    # Block shell metacharacters
    if not _SAFE_PATH_RE.match(cleaned):
        raise GitInputValidationError(
            f"file_path contains disallowed characters: {cleaned!r}. "
            f"Only alphanumerics, '/', '-', '_', '.' are allowed."
        )

    return cleaned


def _validate_sha(commit_sha: str) -> str:
    """Validate a commit SHA — must be 4-40 hex characters."""
    if not commit_sha or not commit_sha.strip():
        raise GitInputValidationError("commit_sha must not be empty")

    cleaned = commit_sha.strip()

    if not _SAFE_SHA_RE.match(cleaned):
        raise GitInputValidationError(
            f"commit_sha contains disallowed characters: {cleaned!r}. "
            f"Only hex digits (0-9, a-f) are allowed."
        )

    return cleaned


def _run_git(args: list[str], repo_path: Path | None = None) -> str:
    """Run a git command and return stdout.  Never uses shell=True."""
    cwd = repo_path or DEFAULT_REPO_PATH

    if not cwd.is_dir():
        raise FileNotFoundError(
            f"Repository directory not found: {cwd}. "
            f"Run `uv run python scripts/setup_victim_repo.py` first."
        )

    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")

    return result.stdout


# ── Public API ────────────────────────────────────────────────────


def get_recent_commits(
    file_path: str,
    limit: int = 5,
    repo_path: Path | None = None,
) -> list[CommitInfo]:
    """Return the *limit* most recent commits touching *file_path*."""
    safe_path = _validate_file_path(file_path)

    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    # --format: sha|short|author|date|subject
    raw = _run_git(
        [
            "log",
            f"-{limit}",
            "--format=%H|%h|%an|%ai|%s",
            "--",
            safe_path,
        ],
        repo_path,
    )

    commits: list[CommitInfo] = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("|", maxsplit=4)
        if len(parts) == 5:
            commits.append(
                CommitInfo(
                    sha=parts[0],
                    short_sha=parts[1],
                    author=parts[2],
                    date=parts[3],
                    message=parts[4],
                )
            )

    return commits


def get_commit_diff(
    commit_sha: str,
    repo_path: Path | None = None,
) -> DiffOutput:
    """Return the full diff for a specific commit."""
    safe_sha = _validate_sha(commit_sha)

    raw = _run_git(["show", "--format=", "--patch", safe_sha], repo_path)

    return DiffOutput(sha=safe_sha, diff=raw)


def get_git_blame(
    file_path: str,
    line_number: int,
    repo_path: Path | None = None,
) -> BlameEntry:
    """Return git-blame info for a specific line in a file."""
    safe_path = _validate_file_path(file_path)

    if line_number < 1:
        raise ValueError("line_number must be >= 1")

    raw = _run_git(
        [
            "blame",
            "-L",
            f"{line_number},{line_number}",
            "--porcelain",
            safe_path,
        ],
        repo_path,
    )

    # Parse porcelain blame output
    commit_sha = ""
    author = ""
    date = ""
    content = ""

    for line in raw.splitlines():
        if not commit_sha and len(line) >= 40 and all(c in "0123456789abcdef" for c in line[:40]):
            commit_sha = line[:40]
        elif line.startswith("author "):
            author = line[len("author "):]
        elif line.startswith("author-time "):
            # Unix timestamp — keep raw for simplicity
            date = line[len("author-time "):]
        elif line.startswith("\t"):
            content = line[1:]  # strip leading tab

    return BlameEntry(
        commit_sha=commit_sha,
        author=author,
        date=date,
        line_number=line_number,
        line_content=content,
    )
