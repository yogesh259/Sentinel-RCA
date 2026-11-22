"""Builds a Postgres connection pool from a database URL."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Neon's pooled endpoint does not support server-side prepared statements.
CONNECT_ARGS = {"prepare_threshold": None}


def create_engine_for(database_url: str, pool_size: int = 5) -> AsyncEngine:
    """Pooled async engine; pre-ping replaces connections the database closed while idle."""
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        pool_pre_ping=True,
        connect_args=CONNECT_ARGS,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory whose loaded objects stay readable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
