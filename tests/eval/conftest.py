"""Pytest configuration for evals suite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Inject default test environment variables before modules import settings
os.environ.setdefault("ENV", "local")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_rca",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("WEBHOOK_SECRET", "local-dev-secret-123")


@pytest.fixture
def eval_mode(request) -> str:
    """Resolve eval mode from CLI flag or EVAL_MODE env var."""
    if request.config.getoption("--live"):
        return "live"
    env_val = os.getenv("EVAL_MODE", "mock").strip().lower()
    return env_val if env_val in ("live", "off", "mock") else "mock"


@pytest.fixture
def live_mode(eval_mode: str) -> bool:
    """Backward-compatible boolean fixture for live LLM mode."""
    return eval_mode == "live"
