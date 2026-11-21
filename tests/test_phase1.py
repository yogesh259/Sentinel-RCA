"""Phase 1 tests — webhook HMAC, fingerprinting, and Redis deduplication.

All tests are deterministic and do NOT require Postgres or a running server.
Redis is mocked with FakeRedis.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Fixtures & helpers ─────────────────────────────────────────────

WEBHOOK_SECRET = "test-secret-key"

SAMPLE_PAYLOAD = {
    "action": "created",
    "data": {
        "event": {
            "event_id": "abc123",
            "title": "NullPointerException: discountCode.length()",
            "culprit": "com.victim.controller.OrderController.createOrder",
            "timestamp": 1700000000.0,
            "exception": {
                "values": [
                    {
                        "type": "NullPointerException",
                        "value": "Cannot invoke String.length() because discountCode is null",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "OrderController.java",
                                    "lineno": 34,
                                    "function": "createOrder",
                                },
                            ],
                        },
                    }
                ]
            },
        }
    },
}

GENERIC_PAYLOAD = {
    "service": "order-service",
    "error": "NullPointerException: discountCode.length()",
    "stack_trace": (
        "NullPointerException: Cannot invoke String.length() because discountCode is null\n"
        "\tat com.victim.controller.OrderController.createOrder(OrderController.java:34)"
    ),
}


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Pure-unit tests (no I/O) ──────────────────────────────────────


class TestFingerprint:
    """Test the deterministic fingerprint hash function."""

    def test_deterministic(self):
        from app.services.redis_dedup import compute_fingerprint

        fp1 = compute_fingerprint("NullPointerException", "OrderController.java", 34)
        fp2 = compute_fingerprint("NullPointerException", "OrderController.java", 34)
        assert fp1 == fp2

    def test_different_inputs_differ(self):
        from app.services.redis_dedup import compute_fingerprint

        fp1 = compute_fingerprint("NullPointerException", "OrderController.java", 34)
        fp2 = compute_fingerprint("NullPointerException", "OrderController.java", 35)
        assert fp1 != fp2

    def test_is_sha256_hex(self):
        from app.services.redis_dedup import compute_fingerprint

        fp = compute_fingerprint("NPE", "Foo.java", 1)
        assert len(fp) == 64  # SHA-256 hex digest


class TestWebhookAuth:
    """Test HMAC-SHA256 verification logic."""

    def test_valid_signature(self):
        from app.services.webhook_auth import verify_signature

        body = b'{"test": true}'
        with patch("app.services.webhook_auth.settings") as mock_settings:
            mock_settings.webhook_secret = WEBHOOK_SECRET
            sig = _sign(body)
            assert verify_signature(body, sig) is True

    def test_invalid_signature(self):
        from app.services.webhook_auth import verify_signature

        body = b'{"test": true}'
        with patch("app.services.webhook_auth.settings") as mock_settings:
            mock_settings.webhook_secret = WEBHOOK_SECRET
            assert verify_signature(body, "deadbeef") is False

    def test_missing_signature(self):
        from app.services.webhook_auth import verify_signature

        with patch("app.services.webhook_auth.settings") as mock_settings:
            mock_settings.webhook_secret = WEBHOOK_SECRET
            assert verify_signature(b"body", None) is False


# ── Integration tests (mocked Redis + Postgres) ───────────────────


class FakeRedis:
    """Minimal in-memory Redis mock supporting SETNX, INCR, EXPIRE, and PING."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self._store:
            return None  # key exists — SETNX returns None
        self._store[key] = value
        return True

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def ping(self) -> bool:
        return True

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Override settings for all tests."""
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")


@pytest.fixture()
def client():
    """TestClient with Redis + Postgres mocked out."""
    fake_redis = FakeRedis()

    async def _fake_get_redis():
        return fake_redis

    # Patch at the module level where the functions are looked up
    with (
        patch("app.services.redis_dedup.get_redis", new=_fake_get_redis),
        patch("app.services.postgres.ensure_tables", new_callable=AsyncMock),
        patch("app.services.postgres.insert_incident", new_callable=AsyncMock, return_value=1),
        patch("app.services.redis_dedup.close_redis", new_callable=AsyncMock),
        patch("app.services.postgres.close_pool", new_callable=AsyncMock),
        patch("app.services.webhook_auth.settings") as mock_auth_settings,
        patch("app.services.redis_dedup.settings") as mock_dedup_settings,
    ):
        mock_auth_settings.webhook_secret = WEBHOOK_SECRET
        mock_dedup_settings.dedup_ttl_seconds = 1800

        from app.main import app

        yield TestClient(app, raise_server_exceptions=False)


class TestWebhookEndpoint:
    """Integration tests for POST /v1/incidents/ingest."""

    def test_rejects_missing_signature(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        resp = client.post("/v1/incidents/ingest", content=body)
        assert resp.status_code == 401

    def test_rejects_invalid_signature(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        resp = client.post(
            "/v1/incidents/ingest",
            content=body,
            headers={"X-Webhook-Signature": "bad-sig"},
        )
        assert resp.status_code == 401

    def test_first_event_queued_202(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        sig = _sign(body)
        resp = client.post(
            "/v1/incidents/ingest",
            content=body,
            headers={"X-Webhook-Signature": sig},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["fingerprint"]
        assert data["llm_cost"] == "$0"

    def test_rate_limit_exceeded_returns_429(self, client):
        """When rate limit is exceeded, return 429 Too Many Requests immediately."""
        body = json.dumps(GENERIC_PAYLOAD).encode()
        sig = _sign(body)

        with patch("app.main.check_rate_limit", new_callable=AsyncMock, return_value=False):
            resp = client.post(
                "/v1/incidents/ingest",
                content=body,
                headers={"X-Webhook-Signature": sig},
            )
            assert resp.status_code == 429
            data = resp.json()
            assert "Rate limit exceeded" in data["detail"]

    def test_generic_payload_queued(self, client):
        body = json.dumps(GENERIC_PAYLOAD).encode()
        sig = _sign(body)
        resp = client.post(
            "/v1/incidents/ingest",
            content=body,
            headers={"X-Webhook-Signature": sig},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["fingerprint"]

    def test_duplicate_event_deduplicated_200(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        sig = _sign(body)

        # First call — queued (202)
        resp1 = client.post(
            "/v1/incidents/ingest",
            content=body,
            headers={"X-Webhook-Signature": sig},
        )
        assert resp1.status_code == 202
        assert resp1.json()["status"] == "queued"

        # Second call — deduplicated (200)
        resp2 = client.post(
            "/v1/incidents/ingest",
            content=body,
            headers={"X-Webhook-Signature": sig},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["status"] == "deduplicated"
        assert data2["llm_cost"] == "$0"
        assert data2["duplicate_count"] >= 1

    def test_five_storm_events(self, client):
        """Simulates the 5-event storm — 1 queued (202), 4 deduplicated (200)."""
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        sig = _sign(body)

        statuses = []
        codes = []
        for _ in range(5):
            resp = client.post(
                "/v1/incidents/ingest",
                content=body,
                headers={"X-Webhook-Signature": sig},
            )
            codes.append(resp.status_code)
            statuses.append(resp.json()["status"])

        assert statuses.count("queued") == 1
        assert statuses.count("deduplicated") == 4
        assert codes[0] == 202
        assert all(c == 200 for c in codes[1:])


class TestSettingsConfiguration:
    def test_settings_types_and_defaults(self):
        from app.core.config import get_settings

        cfg = get_settings()
        assert cfg.env in ("local", "staging", "prod")
        assert isinstance(cfg.simulation_mode, bool)
        assert hasattr(cfg.database_url, "get_secret_value")
        assert hasattr(cfg.redis_url, "get_secret_value")
        assert cfg.max_graph_steps == 6
        assert cfg.dedup_ttl_seconds == 1800

    def test_settings_missing_required_fields_raises(self, monkeypatch):
        from pydantic import ValidationError

        from app.core.config import Settings

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)

        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        errors = exc_info.value.errors()
        fields = {e["loc"][0] for e in errors}
        assert "database_url" in fields
        assert "redis_url" in fields
