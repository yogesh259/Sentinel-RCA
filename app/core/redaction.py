"""Pure-Python PII & secret redaction — deterministic, never LLM-delegated.

Masks JWTs, emails, API keys/secrets, and database connection strings.
Returns both the sanitised text and an audit list of redaction types applied.
"""

from __future__ import annotations

import re
from typing import Any, Final

# ── Compiled patterns (order matters — most specific first) ────────

_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    # JWT Bearer tokens: header.payload.signature (each part is base64url)
    (
        "[REDACTED_JWT]",
        re.compile(r"eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}(?:\.[A-Za-z0-9_\-]+)?=*"),
    ),
    # Database connection strings — postgres://, postgresql://, jdbc:postgresql://,
    # mysql://, jdbc:mysql://, mongodb://, redis://
    (
        "[REDACTED_DB_URI]",
        re.compile(
            r"(?:jdbc:(?:[a-zA-Z0-9_+.-]+:)?|jdbc:)?(?:postgresql|postgres|mysql|mariadb|oracle|sqlserver|mongodb(?:\+srv)?|redis|rediss|sqlite)://[^\s,;\"')}\]]+|jdbc:[a-zA-Z0-9_+.-]+:[^\s,;\"')}\]]+",
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
            r"sk-[A-Za-z0-9]{16,}"  # OpenAI
            r"|(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{16,}"  # Stripe
            r"|sk_[A-Za-z0-9_]{16,}"  # Generic sk_ keys
            r"|ghp_[A-Za-z0-9]{36,}"  # GitHub PAT
            r"|gho_[A-Za-z0-9]{36,}"  # GitHub OAuth
            r"|ghs_[A-Za-z0-9]{36,}"  # GitHub App installation
            r"|ghr_[A-Za-z0-9]{36,}"  # GitHub refresh
            r"|AKIA[A-Z0-9]{16}"  # AWS access key
            r"|xox[bpsa]-[A-Za-z0-9\-]{20,}"  # Slack
            r"|Bearer\s+[A-Za-z0-9_\-.]{20,}"  # Generic Bearer token (non-JWT)
            r")",
        ),
    ),
    # Email addresses (RFC-5322 simplified, supporting internal domains)
    (
        "[REDACTED_EMAIL]",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+(?:\.[A-Za-z0-9\-]{2,})+"),
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


def redact_dict(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Recursively run redact_payload on all string values in a dictionary.

    Returns:
        (sanitised_dict, combined_redaction_types)
    """
    applied_types: list[str] = []

    def _redact_value(val: Any) -> Any:
        if isinstance(val, str):
            sanitized, r_types = redact_payload(val)
            for rt in r_types:
                if rt not in applied_types:
                    applied_types.append(rt)
            return sanitized
        elif isinstance(val, dict):
            return {k: _redact_value(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [_redact_value(item) for item in val]
        elif isinstance(val, tuple):
            return tuple(_redact_value(item) for item in val)
        else:
            return val

    redacted = {k: _redact_value(v) for k, v in payload.items()}
    return redacted, applied_types
