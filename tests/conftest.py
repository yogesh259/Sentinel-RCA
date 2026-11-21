"""Pytest configuration and environment fixtures for Sentinel-RCA test suite."""


import os
from unittest.mock import patch

import pytest

# Inject default test environment variables before modules import settings
os.environ.setdefault("ENV", "local")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_rca",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("WEBHOOK_SECRET", "local-dev-secret-123")
os.environ.setdefault("GROQ_API_KEY", "gsk_mock_test_key_for_testing")
os.environ.setdefault("GEMINI_API_KEY", "gemini_mock_test_key_for_testing")
os.environ.setdefault("GITHUB_TOKEN", "mock_gh_token_for_testing")
os.environ.setdefault("GITHUB_REPO", "octocat/victim-service")

from tests.mocks import create_mock_github_client, mock_llm_complete  # noqa: E402


def pytest_addoption(parser):
    """Register custom CLI options."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run evals using live LLM APIs instead of deterministic mock mode",
    )


@pytest.fixture(autouse=True)
def auto_mock_llm(request):
    """Automatically mock llm_complete for fast, zero-cost tests unless live or marked no_llm_mock."""
    if request.node.get_closest_marker("no_llm_mock"):
        yield
        return

    try:
        if request.config.getoption("--live"):
            yield
            return
    except (ValueError, AttributeError):
        pass

    with patch("src.services.llm_router.llm_complete", side_effect=mock_llm_complete):
        yield


@pytest.fixture(autouse=True)
def auto_mock_github(request):
    """Automatically mock httpx calls in git_mcp for fast, offline tests unless live."""
    try:
        if request.config.getoption("--live"):
            yield
            return
    except (ValueError, AttributeError):
        pass

    with patch("src.mcp_servers.git_mcp._create_client", side_effect=create_mock_github_client):
        yield
