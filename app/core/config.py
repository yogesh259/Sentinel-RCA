"""Sentinel-RCA configuration loaded from environment / .env file."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — only the variables defined in ARCHITECTURE.md §6."""

    # Neon Postgres DSN
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_rca"

    # Upstash Redis URL
    redis_url: str = "redis://localhost:6379/0"

    # Webhook HMAC-SHA256 shared secret
    webhook_secret: SecretStr = Field(default=SecretStr(""))

    # Groq API key — empty means Simulation Mode
    groq_api_key: SecretStr = Field(default=SecretStr(""))

    # Rate limiting
    groq_daily_limit: int = Field(default=30, ge=1, description="Max daily live Groq calls")
    rate_limit_per_hour: int = Field(default=5, ge=1, description="Hourly rate limit per IP")

    # Git repo path for git_mcp
    local_repo_path: str = "./victim-service"

    # MCP auth token (optional — used for Streamable HTTP transport)
    mcp_auth_token: SecretStr = Field(default=SecretStr(""))

    # Circuit breaker
    max_graph_steps: int = Field(default=6, ge=1, le=50, description="Max LangGraph loop iterations")

    # Deduplication fingerprint TTL in seconds (30 minutes)
    dedup_ttl_seconds: int = Field(default=1800, gt=0, description="Redis dedup key TTL in seconds")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
