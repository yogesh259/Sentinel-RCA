"""git_mcp — Read-only MCP server over the GitHub REST + GraphQL APIs.

Zero local disk dependency: everything is fetched with httpx using GITHUB_TOKEN and
GITHUB_REPO — the only secrets this process ever receives. All inputs are validated to
prevent path traversal and injection, and all returned text is redacted before it can
reach an LLM. Run standalone over stdio with `python -m src.mcp_servers.git_mcp`.

Exposes:
    resolve_repo_path(file_name, module_hint) -> str | None
    get_recent_commits(file_path, limit)      -> list[CommitInfo]
    get_commit_diff(commit_sha)               -> DiffOutput
    get_blame_range(file_path, start, end)    -> list[BlameRange]
    get_file_contents(file_path, ref)         -> str
"""


import base64
import logging
import re
import time
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.redaction import redact_payload
from src.schemas.mcp.git import BlameRange, CommitInfo, DiffOutput

logger = logging.getLogger("sentinel-rca.git_mcp")


class GitServerSettings(BaseSettings):
    """The only configuration this server can see (least privilege)."""

    github_token: SecretStr = SecretStr("")
    github_repo: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = GitServerSettings()

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT_S = 10.0
TREE_CACHE_TTL_S = 600.0

# Allow safe characters in file paths: alphanumerics, /, -, _, .
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9/_.\-]+$")

# SHA: 4-40 hex chars
_SAFE_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")

# repo slug -> (fetched_at, blob paths); avoids one tree download per incident.
_TREE_CACHE: dict[str, tuple[float, list[str]]] = {}

_BLAME_QUERY = """
query($owner: String!, $name: String!, $path: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          blame(path: $path) {
            ranges {
              startingLine
              endingLine
              commit { oid committedDate messageHeadline author { name } }
            }
          }
        }
      }
    }
  }
}
"""


class GitInputValidationError(ValueError):
    """Raised when a git_mcp input contains unsafe characters."""


class GitConfigurationError(RuntimeError):
    """Raised when GITHUB_REPO is not configured, so no repository can be queried."""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub API requests fail."""


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub API rate limits or secondary rate limits are exceeded."""


class GitHubNotFoundError(GitHubAPIError, FileNotFoundError):
    """Raised when a requested file or commit does not exist in the repository."""


# ── Input validation ──────────────────────────────────────────────


def _validate_file_path(file_path: str) -> str:
    """Validate and normalise a file path — reject path traversal and metacharacters."""
    if not file_path or not file_path.strip():
        raise GitInputValidationError("file_path must not be empty")

    cleaned = file_path.strip()
    if ".." in cleaned:
        raise GitInputValidationError(
            f"file_path contains path-traversal sequence '..': {cleaned!r}"
        )
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


# ── HTTP helpers ──────────────────────────────────────────────────


def _get_repo() -> str:
    """Return the configured `owner/repo` slug; there is deliberately no default."""
    target = settings.github_repo.strip().strip("/")
    if target.count("/") != 1:
        raise GitConfigurationError("GITHUB_REPO must be set in 'owner/repo' format.")
    return target


def _get_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    """Build GitHub API headers, adding the bearer token when one is configured."""
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Sentinel-RCA",
    }
    token = settings.github_token.get_secret_value().strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _handle_response_errors(resp: httpx.Response, context: str) -> None:
    """Translate GitHub HTTP failures into typed domain errors."""
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
    """Create a default AsyncClient instance (patched in tests with a mock transport)."""
    return httpx.AsyncClient()


async def _send(
    method: str,
    url: str,
    context: str,
    allow_404: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """Send one GitHub request with a timeout and uniform error mapping.

    Returns the raw response; with ``allow_404`` a 404 is returned instead of raised.
    """
    try:
        async with _create_client() as client:
            resp = await client.request(method, url, timeout=REQUEST_TIMEOUT_S, **kwargs)
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"GitHub API request timed out while fetching {context}") from exc

    if not (allow_404 and resp.status_code == 404):
        _handle_response_errors(resp, context)
    return resp


def _redact(text: str) -> str:
    """Redact secrets/PII from repository text before it leaves this server."""
    redacted, _ = redact_payload(text)
    return redacted


# ── Path resolution ───────────────────────────────────────────────


async def list_repo_files() -> list[str]:
    """Return every blob path on the default branch (cached for TREE_CACHE_TTL_S)."""
    repo_slug = _get_repo()
    cached = _TREE_CACHE.get(repo_slug)
    if cached and time.monotonic() - cached[0] < TREE_CACHE_TTL_S:
        return cached[1]

    resp = await _send(
        "GET",
        f"{GITHUB_API}/repos/{repo_slug}/git/trees/HEAD",
        "repository tree",
        params={"recursive": "1"},
        headers=_get_headers(),
    )
    paths = [item["path"] for item in resp.json().get("tree", []) if item.get("type") == "blob"]
    _TREE_CACHE[repo_slug] = (time.monotonic(), paths)
    return paths


def _reference_parts(file_name: str, module_hint: str) -> list[str]:
    """Best-guess path components for a frame, used to rank same-named files.

    `com.acme.OrderController` + `OrderController.java` -> [com, acme, OrderController.java]
    """
    base_name = file_name.rsplit("/", 1)[-1]
    if "/" in file_name:
        return [p for p in file_name.split("/") if p]
    if module_hint:
        return [*module_hint.split(".")[:-1], base_name]
    return [base_name]


def _common_suffix_len(a: list[str], b: list[str]) -> int:
    """Count matching trailing path components of two paths."""
    count = 0
    for x, y in zip(reversed(a), reversed(b), strict=False):
        if x != y:
            break
        count += 1
    return count


async def resolve_repo_path(
    file_name: str,
    module_hint: str = "",
) -> str | None:
    """Map a stack-frame file (bare name or absolute container path) to a repo path.

    Language-agnostic: matches by basename against the repo tree, then prefers the
    candidate sharing the longest path suffix with the frame's package/module hint.
    Returns None when the file isn't in the repository (e.g. a third-party library).
    """
    if not file_name or file_name in ("?", "unknown"):
        return None

    reference = _reference_parts(file_name.strip(), module_hint)
    base_name = reference[-1]
    candidates = [p for p in await list_repo_files() if p.rsplit("/", 1)[-1] == base_name]
    if not candidates:
        return None

    return max(candidates, key=lambda p: (_common_suffix_len(p.split("/"), reference), -len(p)))


# ── Git history tools ─────────────────────────────────────────────


async def get_recent_commits(
    file_path: str,
    limit: int = 5,
) -> list[CommitInfo]:
    """Return the *limit* most recent commits touching *file_path*."""
    safe_path = _validate_file_path(file_path)
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    resp = await _send(
        "GET",
        f"{GITHUB_API}/repos/{_get_repo()}/commits",
        f"commits for {safe_path}",
        allow_404=True,
        params={"path": safe_path, "per_page": limit},
        headers=_get_headers(),
    )
    if resp.status_code == 404:
        return []

    commits: list[CommitInfo] = []
    for item in resp.json():
        sha = item.get("sha", "")
        commit_obj = item.get("commit", {})
        author_obj = commit_obj.get("author", {}) or {}
        commits.append(
            CommitInfo(
                sha=sha,
                short_sha=sha[:7],
                author=author_obj.get("name", "Unknown"),
                date=author_obj.get("date", ""),
                message=_redact(commit_obj.get("message", "")).strip(),
            )
        )
    return commits


async def get_commit_diff(
    commit_sha: str,
) -> DiffOutput:
    """Return the unified diff for a specific commit."""
    safe_sha = _validate_sha(commit_sha)
    resp = await _send(
        "GET",
        f"{GITHUB_API}/repos/{_get_repo()}/commits/{safe_sha}",
        f"commit diff for {safe_sha}",
        headers=_get_headers(accept="application/vnd.github.diff"),
    )
    return DiffOutput(sha=safe_sha, diff=_redact(resp.text))


async def get_file_contents(
    file_path: str,
    ref: str = "",
) -> str:
    """Return a file's text at *ref* (default branch when *ref* is empty)."""
    safe_path = _validate_file_path(file_path)
    resp = await _send(
        "GET",
        f"{GITHUB_API}/repos/{_get_repo()}/contents/{safe_path}",
        f"file content for {safe_path}",
        params={"ref": ref} if ref else None,
        headers=_get_headers(accept="application/vnd.github.raw+json"),
    )

    text = resp.text
    if "json" in resp.headers.get("content-type", ""):
        data = resp.json()
        if isinstance(data, dict) and data.get("encoding") == "base64":
            text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return _redact(text)


async def get_blame_range(
    file_path: str,
    start_line: int,
    end_line: int,
) -> list[BlameRange]:
    """Return real line-level blame for [start_line, end_line] on the default branch.

    Uses the GitHub GraphQL `blame` field (the REST API has no blame endpoint), so the
    commits returned are the ones that last changed exactly these lines.
    """
    safe_path = _validate_file_path(file_path)
    if start_line < 1 or end_line < start_line:
        raise ValueError("Require 1 <= start_line <= end_line")

    owner, name = _get_repo().split("/")
    resp = await _send(
        "POST",
        f"{GITHUB_API}/graphql",
        f"blame for {safe_path}",
        headers=_get_headers(),
        json={"query": _BLAME_QUERY, "variables": {"owner": owner, "name": name, "path": safe_path}},
    )
    payload = resp.json()
    if payload.get("errors"):
        raise GitHubAPIError(f"GitHub GraphQL blame failed for {safe_path}: {payload['errors']}")

    branch = ((payload.get("data") or {}).get("repository") or {}).get("defaultBranchRef") or {}
    ranges = ((branch.get("target") or {}).get("blame") or {}).get("ranges", [])

    blamed: list[BlameRange] = []
    for r in ranges:
        if r["endingLine"] < start_line or r["startingLine"] > end_line:
            continue
        commit = r["commit"]
        blamed.append(
            BlameRange(
                commit_sha=commit["oid"],
                short_sha=commit["oid"][:7],
                author=(commit.get("author") or {}).get("name", "Unknown"),
                date=commit.get("committedDate", ""),
                message=_redact(commit.get("messageHeadline", "")).strip(),
                start_line=r["startingLine"],
                end_line=r["endingLine"],
            )
        )
    return blamed


server = MCPServer("git_mcp")
for _tool in (
    resolve_repo_path,
    get_recent_commits,
    get_commit_diff,
    get_file_contents,
    get_blame_range,
):
    server.add_tool(_tool)


if __name__ == "__main__":
    server.run()
