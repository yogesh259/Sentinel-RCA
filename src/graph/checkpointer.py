"""Durable LangGraph checkpointing on Postgres (Neon).

HITL pauses can last hours; Render's free tier restarts idle containers. Persisting
checkpoints in Postgres is what lets an approval survive a restart and resume exactly
where the graph stopped. The checkpoint tables are created by src/db/migrate.py.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.core.config import config

logger = logging.getLogger("sentinel-rca.graph.checkpointer")

# autocommit is required by AsyncPostgresSaver; prepare_threshold=0 disables server-side
# prepared statements, which break behind Neon's PgBouncer-based pooled endpoint.
_CONNECTION_KWARGS = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    """Yield a Postgres checkpointer for the app's lifetime.

    Only ENV=local may fall back to in-memory checkpoints (and says so loudly);
    staging/prod refuse to start without durable HITL state.
    """
    pool = AsyncConnectionPool(
        conninfo=config.database_url.get_secret_value(), max_size=5, open=False, kwargs=_CONNECTION_KWARGS
    )
    try:
        await pool.open(wait=True, timeout=10)
        saver = AsyncPostgresSaver(pool)
    except Exception as exc:
        await pool.close()
        if config.env != "local":
            raise
        logger.warning(
            "Postgres checkpointer unavailable (%s). Using MemorySaver: pending approvals "
            "will be LOST on restart. This fallback is only allowed with ENV=local.",
            exc,
        )
        yield MemorySaver()
        return

    try:
        logger.info("LangGraph checkpoints persisted to Postgres.")
        yield saver
    finally:
        await pool.close()


__all__ = ["open_checkpointer"]
