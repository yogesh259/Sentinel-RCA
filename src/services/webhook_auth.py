"""HMAC-SHA256 webhook signature verification — pure Python, never LLM."""


import hashlib
import hmac

from src.core.config import config


def verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Verify the X-Webhook-Signature header against the shared secret.

    Sentry signs the raw body with HMAC-SHA256 and sends the hex digest
    in the ``X-Webhook-Signature`` header.

    Returns True if the signature is valid.
    """
    if not signature_header:
        return False

    secret = (
        config.webhook_secret.get_secret_value()
        if hasattr(config.webhook_secret, "get_secret_value")
        else str(config.webhook_secret)
    )

    expected = hmac.new(
        secret.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)
