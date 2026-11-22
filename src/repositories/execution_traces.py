"""Repository for the execution_traces audit table."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ExecutionTraceRow


async def add_trace(session: AsyncSession, **fields: Any) -> ExecutionTraceRow:
    """Insert one run's audit record and return it with its generated id."""
    row = ExecutionTraceRow(**fields)
    session.add(row)
    await session.flush()
    return row


async def update_trace(session: AsyncSession, thread_id: str, **fields: Any) -> None:
    """Overwrite the given columns on a thread's trace (e.g. after an approval resumes it)."""
    await session.execute(
        update(ExecutionTraceRow).where(ExecutionTraceRow.thread_id == thread_id).values(**fields)
    )


async def list_latest(session: AsyncSession, limit: int) -> list[ExecutionTraceRow]:
    """Most recent traces first."""
    query = select(ExecutionTraceRow).order_by(ExecutionTraceRow.created_at.desc()).limit(limit)
    return list(await session.scalars(query))
