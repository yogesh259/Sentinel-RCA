"""tracker_mcp — Standalone MCP server for issue tracking via Postgres.

Exposes:
    search_existing_issues(exception_type, file_name) -> list[IssueRecord]
    create_issue(title, root_cause, commit_sha, severity) -> IssueRecord
    draft_hotfix_pr(commit_sha, file_path, patch_summary) -> PRPreview
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from app.core.config import settings
from app.schemas.mcp.tracker import IssueRecord, PRPreview

# asyncpg expects plain postgresql:// DSN
_DSN_CLEAN = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    """Return (and cache) the asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_DSN_CLEAN, min_size=2, max_size=10)
    return _pool


_table_ensured = False


async def ensure_issues_table() -> None:
    """Create the issues table if it does not exist."""
    global _table_ensured
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS issues (
                id              SERIAL PRIMARY KEY,
                title           TEXT NOT NULL,
                exception_type  TEXT NOT NULL,
                file_name       TEXT NOT NULL DEFAULT '',
                root_cause      TEXT NOT NULL DEFAULT '',
                commit_sha      TEXT NOT NULL DEFAULT '',
                severity        TEXT NOT NULL DEFAULT 'medium',
                status          TEXT NOT NULL DEFAULT 'open',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_issues_exception_file
            ON issues (exception_type, file_name);
            """
        )
    _table_ensured = True


async def _ensure_table_once() -> None:
    global _table_ensured
    if not _table_ensured:
        try:
            await ensure_issues_table()
        except Exception:
            pass


# ── Public API ────────────────────────────────────────────────────


async def search_existing_issues(
    exception_type: str,
    file_name: str,
) -> list[IssueRecord]:
    """Find existing issues matching the exception type and file name."""
    await _ensure_table_once()
    pool = await _get_pool()
    base_file = Path(file_name).name if file_name else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, exception_type, file_name, root_cause,
                   commit_sha, severity, status, created_at
            FROM issues
            WHERE exception_type = $1
              AND (
                  file_name = $2
                  OR ($3 <> '' AND (file_name = $3 OR file_name LIKE '%' || $3))
              )
            ORDER BY created_at DESC
            LIMIT 20;
            """,
            exception_type,
            file_name,
            base_file,
        )

    return [
        IssueRecord(
            id=r["id"],
            title=r["title"],
            exception_type=r["exception_type"],
            file_name=r["file_name"],
            root_cause=r["root_cause"],
            commit_sha=r["commit_sha"],
            severity=r["severity"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


async def create_issue(
    title: str,
    root_cause: str,
    commit_sha: str,
    severity: str,
    exception_type: str = "",
    file_name: str = "",
) -> IssueRecord:
    """Insert a new issue and return the created record."""
    if severity not in ("low", "medium", "high", "critical"):
        raise ValueError(f"severity must be one of low/medium/high/critical, got {severity!r}")

    await _ensure_table_once()
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO issues (title, exception_type, file_name, root_cause, commit_sha, severity)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, title, exception_type, file_name, root_cause,
                      commit_sha, severity, status, created_at;
            """,
            title,
            exception_type,
            file_name,
            root_cause,
            commit_sha,
            severity,
        )

    return IssueRecord(
        id=row["id"],
        title=row["title"],
        exception_type=row["exception_type"],
        file_name=row["file_name"],
        root_cause=row["root_cause"],
        commit_sha=row["commit_sha"],
        severity=row["severity"],
        status=row["status"],
        created_at=row["created_at"],
    )


async def draft_hotfix_pr(
    commit_sha: str,
    file_path: str,
    patch_summary: str,
) -> PRPreview:
    """Return a structured PR preview object (stubbed — no actual PR created).

    In a production deployment this would call GitHub/GitLab APIs after
    human-in-the-loop approval.
    """
    # Sanitise branch name from the commit SHA
    short_sha = commit_sha[:7] if len(commit_sha) >= 7 else commit_sha
    branch_name = f"hotfix/{short_sha}"

    # Derive a title from the file and summary
    file_base = file_path.rsplit("/", maxsplit=1)[-1] if "/" in file_path else file_path
    title = f"fix({file_base}): {patch_summary[:80]}"

    description = (
        f"## Hotfix PR (Draft)\n\n"
        f"**Root-cause commit:** `{commit_sha}`\n\n"
        f"**File:** `{file_path}`\n\n"
        f"### Summary\n{patch_summary}\n\n"
        f"---\n"
        f"_Auto-generated by Sentinel-RCA. Requires human approval before merge._"
    )

    return PRPreview(
        title=title,
        branch_name=branch_name,
        commit_sha=commit_sha,
        file_path=file_path,
        patch_summary=patch_summary,
        description=description,
        status="draft",
    )


async def close_pool() -> None:
    """Cleanly close the asyncpg pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
