"""Repository for the issues table (used only inside the tracker_mcp process)."""

from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import IssueRow

ACTIVE_STATUSES = ("open", "in_progress")


async def find_dedup_candidates(
    session: AsyncSession, exception_type: str, file_name: str, limit: int
) -> list[IssueRow]:
    """Active issues sharing the exception type, the file path, or the file's base name."""
    matches = [IssueRow.exception_type == exception_type]
    if file_name:
        matches.append(IssueRow.file_name == file_name)
        # Paths may be stored repo-relative or bare; match on the base name too.
        matches.append(IssueRow.file_name.endswith(Path(file_name).name, autoescape=True))

    query = (
        select(IssueRow)
        .where(IssueRow.status.in_(ACTIVE_STATUSES), or_(*matches))
        .order_by(IssueRow.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(query))


async def add_issue(session: AsyncSession, **fields: str) -> IssueRow:
    """Insert an issue and return it with its generated id, status and timestamp."""
    row = IssueRow(**fields)
    session.add(row)
    await session.flush()
    return row
