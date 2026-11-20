"""Sentinel-RCA configuration loaded from environment / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings — only the variables defined in ARCHITECTURE.md §6."""

    # Neon Postgres DSN
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_rca"

    # Upstash Redis URL
    redis_url: str = "redis://localhost:6379/0"

    # Webhook HMAC-SHA256 shared secret
    webhook_secret: str = ""

    # Groq API key — empty means Simulation Mode
    groq_api_key: str = ""

    # Rate limiting
    groq_daily_limit: int = 30
    rate_limit_per_hour: int = 5

    # Git repo path for git_mcp
    local_repo_path: str = "./victim-service"

    # MCP auth token (optional — used for Streamable HTTP transport)
    mcp_auth_token: str = ""

    # Circuit breaker
    max_graph_steps: int = 6

    # Deduplication fingerprint TTL in seconds (30 minutes)
    dedup_ttl_seconds: int = 1800

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
