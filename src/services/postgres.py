"""Thin async Postgres layer for incident persistence (asyncpg)."""


import asyncpg

from src.core.config import config
from src.core.dsn import to_plain_dsn

_pool: asyncpg.Pool | None = None


def postgres_dsn() -> str:
    """DATABASE_URL as a plain `postgresql://` DSN, accepted by both asyncpg and psycopg."""
    return to_plain_dsn(config.database_url.get_secret_value())


async def get_pool() -> asyncpg.Pool:
    """Return (and cache) the asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(postgres_dsn(), min_size=2, max_size=10)
    return _pool


async def ensure_tables() -> None:
    """Create the incidents and issues tables if not present."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id            SERIAL PRIMARY KEY,
                fingerprint   TEXT NOT NULL,
                exception_type TEXT NOT NULL,
                file_name     TEXT NOT NULL,
                line_number   INTEGER NOT NULL,
                title         TEXT NOT NULL DEFAULT '',
                raw_payload   JSONB NOT NULL DEFAULT '{}',
                status        TEXT NOT NULL DEFAULT 'queued',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_incidents_fingerprint
            ON incidents (fingerprint);
            """
        )
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


# Alias for explicit database initialization
init_db = ensure_tables


async def insert_incident(
    fingerprint: str,
    exception_type: str,
    file_name: str,
    line_number: int,
    title: str,
    raw_payload: dict,
) -> int:
    """Insert a new incident row and return its id."""
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        row_id: int = await conn.fetchval(
            """
            INSERT INTO incidents (
                fingerprint, exception_type, file_name, line_number, title, raw_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id;
            """,
            fingerprint,
            exception_type,
            file_name,
            line_number,
            title,
            json.dumps(raw_payload),
        )
    return row_id


async def close_pool() -> None:
    """Cleanly close the asyncpg pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
