"""Phase 1 tests — webhook HMAC, fingerprinting, and Redis deduplication.

All tests are deterministic and do NOT require Postgres or a running server.
Redis is mocked with fakeredis.
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
    """Minimal in-memory Redis mock supporting SETNX-style set + incr."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self._store:
            return None  # key exists — SETNX returns None
        self._store[key] = value
        return True

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

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
    """Integration tests for POST /v1/webhooks/sentry."""

    def test_rejects_missing_signature(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        resp = client.post("/v1/webhooks/sentry", content=body)
        assert resp.status_code == 401

    def test_rejects_invalid_signature(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        resp = client.post(
            "/v1/webhooks/sentry",
            content=body,
            headers={"X-Webhook-Signature": "bad-sig"},
        )
        assert resp.status_code == 401

    def test_first_event_queued(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        sig = _sign(body)
        resp = client.post(
            "/v1/webhooks/sentry",
            content=body,
            headers={"X-Webhook-Signature": sig},
        )
        assert resp.status_code == 200  # FastAPI returns 200 for response_model
        data = resp.json()
        assert data["status"] == "queued"
        assert data["fingerprint"]
        assert data["llm_cost"] == "$0"

    def test_duplicate_event_deduplicated(self, client):
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        sig = _sign(body)

        # First call — queued
        resp1 = client.post(
            "/v1/webhooks/sentry",
            content=body,
            headers={"X-Webhook-Signature": sig},
        )
        assert resp1.json()["status"] == "queued"

        # Second call — deduplicated
        resp2 = client.post(
            "/v1/webhooks/sentry",
            content=body,
            headers={"X-Webhook-Signature": sig},
        )
        data2 = resp2.json()
        assert data2["status"] == "deduplicated"
        assert data2["llm_cost"] == "$0"
        assert data2["duplicate_count"] >= 1

    def test_five_storm_events(self, client):
        """Simulates the 5-event storm — only 1 should be queued."""
        body = json.dumps(SAMPLE_PAYLOAD).encode()
        sig = _sign(body)

        statuses = []
        for _ in range(5):
            resp = client.post(
                "/v1/webhooks/sentry",
                content=body,
                headers={"X-Webhook-Signature": sig},
            )
            statuses.append(resp.json()["status"])

        assert statuses.count("queued") == 1
        assert statuses.count("deduplicated") == 4
        assert statuses[0] == "queued"
