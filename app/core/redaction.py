"""Pure-Python PII & secret redaction — deterministic, never LLM-delegated.

Masks JWTs, emails, API keys/secrets, and database connection strings.
Returns both the sanitised text and an audit list of redaction types applied.
"""

from __future__ import annotations

import re
from typing import Final

# ── Compiled patterns (order matters — most specific first) ────────

_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    # JWT Bearer tokens: header.payload.signature (each part is base64url)
    (
        "[REDACTED_JWT]",
        re.compile(
            r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
    ),
    # Database connection strings — postgres://, postgresql://, jdbc:postgresql://,
    # mysql://, jdbc:mysql://, mongodb://, redis://
    (
        "[REDACTED_DB_URI]",
        re.compile(
            r"(?:jdbc:)?(?:postgresql|postgres|mysql|mongodb(?:\+srv)?|redis|rediss)"
            r"://[^\s,;\"')}\]]+",
            re.IGNORECASE,
        ),
    ),
    # API keys / secrets — common vendor prefixes
    # OpenAI sk-..., GitHub ghp_/gho_/ghs_/ghr_, AWS AKIA...,
    # Stripe sk_live_/sk_test_/pk_live_/pk_test_, Slack xoxb-/xoxp-/xoxs-
    (
        "[REDACTED_API_KEY]",
        re.compile(
            r"(?:"
            r"sk-[A-Za-z0-9]{20,}"              # OpenAI
            r"|ghp_[A-Za-z0-9]{36,}"             # GitHub PAT
            r"|gho_[A-Za-z0-9]{36,}"             # GitHub OAuth
            r"|ghs_[A-Za-z0-9]{36,}"             # GitHub App installation
            r"|ghr_[A-Za-z0-9]{36,}"             # GitHub refresh
            r"|AKIA[A-Z0-9]{16}"                  # AWS access key
            r"|(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{20,}"  # Stripe
            r"|xox[bpsa]-[A-Za-z0-9\-]{20,}"     # Slack
            r"|Bearer\s+[A-Za-z0-9_\-.]{20,}"    # Generic Bearer token (non-JWT)
            r")",
        ),
    ),
    # Email addresses (RFC-5322 simplified)
    (
        "[REDACTED_EMAIL]",
        re.compile(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
        ),
    ),
]


def redact_payload(text: str) -> tuple[str, list[str]]:
    """Mask sensitive data in *text*.

    Returns:
        (sanitised_text, redaction_types_applied)

    ``redaction_types_applied`` contains each redaction label that fired
    (e.g. ``["[REDACTED_JWT]", "[REDACTED_EMAIL]"]``), de-duplicated and
    in the order they were first matched — useful for audit logging.
    """
    redaction_types: list[str] = []
    sanitised = text

    for label, pattern in _PATTERNS:
        if pattern.search(sanitised):
            sanitised = pattern.sub(label, sanitised)
            if label not in redaction_types:
                redaction_types.append(label)

    return sanitised, redaction_types
