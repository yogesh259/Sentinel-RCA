"""Repository for the incidents table."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import IncidentRow


async def add_incident(
    session: AsyncSession,
    *,
    fingerprint: str,
    exception_type: str,
    file_name: str,
    line_number: int,
    title: str,
    redacted_payload: dict[str, Any],
) -> IncidentRow:
    """Insert an incident and return it with its generated id."""
    row = IncidentRow(
        fingerprint=fingerprint,
        exception_type=exception_type,
        file_name=file_name,
        line_number=line_number,
        title=title,
        redacted_payload=redacted_payload,
    )
    session.add(row)
    await session.flush()  # INSERT ... RETURNING now, so row.id is set before commit
    return row
