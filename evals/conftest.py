"""Pytest configuration for evals suite."""

import sys
from pathlib import Path

import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run evals using live LLM APIs instead of deterministic mock mode",
    )


@pytest.fixture
def live_mode(request):
    return request.config.getoption("--live")
