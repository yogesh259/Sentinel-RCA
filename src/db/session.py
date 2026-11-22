"""The API process's shared connection pool and its transaction scope (session_scope)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.config import config
from src.db.engine import create_engine_for, make_session_factory


@cache
def get_engine() -> AsyncEngine:
    """The API's single connection pool, created on first use."""
    return create_engine_for(config.database_url.get_secret_value(), pool_size=10)


@cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return make_session_factory(get_engine())


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A session in one transaction: commit on success, roll back on error."""
    async with get_session_factory().begin() as session:
        yield session


async def dispose_engine() -> None:
    """Close pooled connections on shutdown."""
    await get_engine().dispose()
