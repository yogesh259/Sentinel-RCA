"""Pytest configuration and environment fixtures for Sentinel-RCA test suite."""

from __future__ import annotations

import os

import pytest

# Inject default test environment variables before modules import settings
os.environ.setdefault("ENV", "local")
os.environ.setdefault("SIMULATION_MODE", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_rca",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("WEBHOOK_SECRET", "local-dev-secret-123")

from app.core.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def reset_settings_cache():
    """Ensure clean settings cache before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
