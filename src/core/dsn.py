"""Config-free DSN helper shared by the API process and the tracker MCP subprocess."""


def to_plain_dsn(database_url: str) -> str:
    """Strip the SQLAlchemy-style `+asyncpg` driver suffix; asyncpg and psycopg want plain DSNs."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")
