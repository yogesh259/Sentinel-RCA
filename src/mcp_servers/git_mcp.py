"""git_mcp — Cloud-native Standalone MCP server for GitHub REST API git operations.

Replaces local git subprocess commands with async httpx calls to GitHub REST API.
All inputs are strictly validated to prevent path traversal and injection.

Exposes:
    get_recent_commits(file_path, limit) -> list[CommitInfo]
    get_commit_diff(commit_sha)          -> DiffOutput
    get_git_blame(file_path, line_number) -> BlameEntry
    get_file_contents(file_path, ref)    -> str
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.core.config import settings
from src.core.redaction import redact_payload
from src.schemas.mcp.git import BlameEntry, CommitInfo, DiffOutput

logger = logging.getLogger("sentinel-rca.git_mcp")

# ── Input validation ──────────────────────────────────────────────

# Allow safe characters in file paths: alphanumerics, /, -, _, .
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9/_.\-]+$")

# SHA: 4-40 hex chars
_SAFE_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


class GitInputValidationError(ValueError):
    """Raised when a git_mcp input contains unsafe characters."""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub REST API requests fail."""


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub REST API rate limits or secondary rate limits are exceeded."""


class GitHubNotFoundError(GitHubAPIError, FileNotFoundError):
    """Raised when a requested file or commit does not exist in the repository."""


def _validate_file_path(file_path: str) -> str:
    """Validate and normalise a file path — reject path traversal and metacharacters."""
    if not file_path or not file_path.strip():
        raise GitInputValidationError("file_path must not be empty")

    cleaned = file_path.strip()

    # Block path traversal
    if ".." in cleaned:
        raise GitInputValidationError(
            f"file_path contains path-traversal sequence '..': {cleaned!r}"
        )

    # Block disallowed characters
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


def resolve_victim_file(file_path: str, repo_path: Path | None = None) -> str:
    """Resolve a bare Java filename to a standard package path."""
    if not file_path or not file_path.strip():
        return ""

    cleaned = file_path.strip().lstrip("/")
    if cleaned in ("?", "unknown"):
        return ""

    if "/" in cleaned:
        return cleaned

    base_name = Path(cleaned).name
    if "controller" in base_name.lower():
        return f"src/main/java/com/victim/controller/{base_name}"
    if "service" in base_name.lower() or "payment" in base_name.lower():
        return f"src/main/java/com/victim/service/{base_name}"

    return cleaned


# ── HTTP Helpers ──────────────────────────────────────────────────


def _get_repo(repo: str | None = None) -> str:
    target = repo or settings.github_repo
    if not target or not target.strip():
        return "octocat/victim-service"
    return target.strip().strip("/")


def _get_headers(token: str | None = None, accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Sentinel-RCA",
    }
    raw_token = token
    if raw_token is None:
        raw_token = (
            settings.github_token.get_secret_value()
            if hasattr(settings.github_token, "get_secret_value")
            else str(settings.github_token)
        )
    if raw_token and raw_token.strip():
        headers["Authorization"] = f"Bearer {raw_token.strip()}"
    return headers


def _handle_response_errors(resp: httpx.Response, context: str) -> None:
    if resp.status_code == 401:
        raise GitHubAPIError(f"GitHub API authentication failed (HTTP 401) for {context}. Check GITHUB_TOKEN.")
    if resp.status_code in (403, 429):
        raise GitHubRateLimitError(
            f"GitHub API rate limit exceeded (HTTP {resp.status_code}) for {context}: {resp.text}"
        )
    if resp.status_code == 404:
        raise GitHubNotFoundError(f"GitHub resource not found (HTTP 404) for {context}: {resp.text}")
    if resp.status_code >= 400:
        raise GitHubAPIError(f"GitHub API error (HTTP {resp.status_code}) for {context}: {resp.text}")


def _create_client() -> httpx.AsyncClient:
    """Create a default AsyncClient instance (can be patched in tests)."""
    return httpx.AsyncClient()


# ── Public API ────────────────────────────────────────────────────


async def get_recent_commits(
    file_path: str,
    limit: int = 5,
    repo: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[CommitInfo]:
    """Return the *limit* most recent commits touching *file_path* via GitHub REST API."""
    safe_path = _validate_file_path(file_path)

    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    repo_slug = _get_repo(repo)
    headers = _get_headers()
    url = f"https://api.github.com/repos/{repo_slug}/commits"
    params = {"path": safe_path, "per_page": limit}

    async def _fetch(c: httpx.AsyncClient) -> httpx.Response:
        return await c.get(url, params=params, headers=headers, timeout=10.0)

    try:
        if client:
            resp = await _fetch(client)
        else:
            async with _create_client() as default_client:
                resp = await _fetch(default_client)
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"GitHub API request timed out while fetching commits for {safe_path}") from exc

    if resp.status_code == 404:
        return []

    _handle_response_errors(resp, f"commits for {safe_path}")

    raw_commits: list[dict[str, Any]] = resp.json()
    commits: list[CommitInfo] = []

    for item in raw_commits:
        sha = item.get("sha", "")
        commit_obj = item.get("commit", {})
        author_obj = commit_obj.get("author", {}) or {}
        raw_msg = commit_obj.get("message", "")
        redacted_msg, _ = redact_payload(raw_msg)

        commits.append(
            CommitInfo(
                sha=sha,
                short_sha=sha[:7] if sha else "",
                author=author_obj.get("name", "Unknown"),
                date=author_obj.get("date", ""),
                message=redacted_msg.strip(),
            )
        )

    return commits


async def get_commit_diff(
    commit_sha: str,
    repo: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> DiffOutput:
    """Return the unified diff for a specific commit via GitHub REST API."""
    safe_sha = _validate_sha(commit_sha)
    repo_slug = _get_repo(repo)
    headers = _get_headers(accept="application/vnd.github.diff")
    url = f"https://api.github.com/repos/{repo_slug}/commits/{safe_sha}"

    async def _fetch(c: httpx.AsyncClient) -> httpx.Response:
        return await c.get(url, headers=headers, timeout=10.0)

    try:
        if client:
            resp = await _fetch(client)
        else:
            async with _create_client() as default_client:
                resp = await _fetch(default_client)
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"GitHub API request timed out while fetching diff for {safe_sha}") from exc

    _handle_response_errors(resp, f"commit diff for {safe_sha}")

    diff_text, _ = redact_payload(resp.text)
    return DiffOutput(sha=safe_sha, diff=diff_text)


async def get_file_contents(
    file_path: str,
    ref: str = "main",
    repo: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return file content string for a path at a given ref via GitHub REST API."""
    safe_path = _validate_file_path(file_path)
    repo_slug = _get_repo(repo)
    headers = _get_headers(accept="application/vnd.github.raw+json")
    url = f"https://api.github.com/repos/{repo_slug}/contents/{safe_path}"
    params = {"ref": ref}

    async def _fetch(c: httpx.AsyncClient) -> httpx.Response:
        return await c.get(url, params=params, headers=headers, timeout=10.0)

    try:
        if client:
            resp = await _fetch(client)
        else:
            async with _create_client() as default_client:
                resp = await _fetch(default_client)
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"GitHub API request timed out while fetching content for {safe_path}") from exc

    _handle_response_errors(resp, f"file content for {safe_path}")

    # If raw returned plain text
    content_type = resp.headers.get("content-type", "")
    if "json" in content_type:
        try:
            data = resp.json()
            if isinstance(data, dict) and "content" in data and data.get("encoding") == "base64":
                raw_bytes = base64.b64decode(data["content"])
                text = raw_bytes.decode("utf-8", errors="replace")
            else:
                text = resp.text
        except Exception:
            text = resp.text
    else:
        text = resp.text

    redacted_text, _ = redact_payload(text)
    return redacted_text


async def get_git_blame(
    file_path: str,
    line_number: int,
    repo: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> BlameEntry:
    """Return line-level blame info by correlating recent commits and file contents."""
    safe_path = _validate_file_path(file_path)
    if line_number < 1:
        raise ValueError("line_number must be >= 1")

    # Step 1: Fetch recent commit for this file
    commits = await get_recent_commits(safe_path, limit=1, repo=repo, client=client)
    latest_commit = commits[0] if commits else None

    # Step 2: Fetch file content to get the line content
    line_content = ""
    try:
        content = await get_file_contents(
            safe_path,
            ref=latest_commit.sha if latest_commit else "main",
            repo=repo,
            client=client,
        )
        lines = content.splitlines()
        if 1 <= line_number <= len(lines):
            line_content = lines[line_number - 1]
        else:
            line_content = f"// Source line {line_number}"
    except Exception:
        line_content = f"// Source line {line_number}"

    return BlameEntry(
        commit_sha=latest_commit.sha if latest_commit else "unknown",
        author=latest_commit.author if latest_commit else "Unknown",
        date=latest_commit.date if latest_commit else "",
        line_number=line_number,
        line_content=line_content,
    )
