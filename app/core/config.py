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

    # LLM routing toggle: true = use local Ollama, false = use hosted API
    use_local_ollama: bool = True

    # Deduplication fingerprint TTL in seconds (30 minutes)
    dedup_ttl_seconds: int = 1800

    # ── LLM Configuration ──────────────────────────────────────────
    # Triage tier (local Ollama)
    ollama_triage_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"

    # Triage tier (hosted — when USE_LOCAL_OLLAMA=false)
    hosted_triage_model: str = "gemini-2.0-flash"

    # Deep RCA tier (frontier model)
    deep_rca_model: str = "gemini-2.0-flash"

    # API keys
    google_api_key: str = ""
    openai_api_key: str = ""

    # Circuit breaker
    max_graph_steps: int = 6

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
