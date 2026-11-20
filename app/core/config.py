"""Sentinel-RCA configuration loaded from environment / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with defaults suitable for local development."""

    # Webhook HMAC-SHA256 shared secret
    webhook_secret: str = "change-me-to-a-real-secret"

    # Redis URL for storm suppression
    redis_url: str = "redis://localhost:6379/0"

    # Postgres DSN (asyncpg driver)
    database_url: str = (
        "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_rca"
    )

    # LLM routing toggle
    use_local_ollama: bool = True

    # Deduplication fingerprint TTL in seconds (30 minutes)
    dedup_ttl_seconds: int = 1800

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
