"""HMAC-SHA256 webhook signature verification — pure Python, never LLM."""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import settings


def verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Verify the X-Webhook-Signature header against the shared secret.

    Sentry signs the raw body with HMAC-SHA256 and sends the hex digest
    in the ``X-Webhook-Signature`` header.

    Returns True if the signature is valid.
    """
    if not signature_header:
        return False

    secret = (
        settings.webhook_secret.get_secret_value()
        if hasattr(settings.webhook_secret, "get_secret_value")
        else str(settings.webhook_secret)
    )

    expected = hmac.new(
        secret.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)
