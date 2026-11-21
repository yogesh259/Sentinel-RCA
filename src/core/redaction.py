"""Deterministic secret redaction using Yelp's detect-secrets + email masking.

Masks secrets (JWTs, API keys, DB URIs, etc.) via detect-secrets scanners and
emails via a lightweight regex. Returns both the sanitised text and an audit
list of redaction types applied.
"""


import re
from typing import Any

from detect_secrets.core.scan import scan_line
from detect_secrets.settings import transient_settings

# detect-secrets plugin configuration for production secret detection
_DETECT_SECRETS_CONFIG = {
    "plugins_used": [
        {"name": "JwtTokenDetector"},
        {"name": "AWSKeyDetector"},
        {"name": "GitHubTokenDetector"},
        {"name": "OpenAIDetector"},
        {"name": "StripeDetector"},
        {"name": "SlackDetector"},
        {"name": "BasicAuthDetector"},
        {"name": "PrivateKeyDetector"},
    ]
}

# Targeted redaction patterns (order: most specific first)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # JWT Bearer tokens: header.payload.signature
    (
        "[REDACTED_JWT]",
        re.compile(r"eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}(?:\.[A-Za-z0-9_\-]+)?=*"),
    ),
    # Database connection strings (JDBC, Postgres, MySQL, MongoDB, Redis, etc.)
    (
        "[REDACTED_DB_URI]",
        re.compile(
            r"(?:jdbc:(?:[a-zA-Z0-9_+.-]+:)?|jdbc:)?(?:postgresql|postgres|mysql|mariadb|oracle|sqlserver|mongodb(?:\+srv)?|redis|rediss|sqlite)://[^\s,;\"')}\]]+|jdbc:[a-zA-Z0-9_+.-]+:[^\s,;\"')}\]]+",
            re.IGNORECASE,
        ),
    ),
    # API keys / secrets (OpenAI, Stripe, GitHub, AWS, Slack, Bearer tokens)
    (
        "[REDACTED_API_KEY]",
        re.compile(
            r"(?:"
            r"sk-[A-Za-z0-9]{16,}"
            r"|(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{16,}"
            r"|sk_[A-Za-z0-9_]{16,}"
            r"|ghp_[A-Za-z0-9]{36,}"
            r"|gho_[A-Za-z0-9]{36,}"
            r"|ghs_[A-Za-z0-9]{36,}"
            r"|ghr_[A-Za-z0-9]{36,}"
            r"|AKIA[A-Z0-9]{16}"
            r"|xox[bpsa]-[A-Za-z0-9\-]{20,}"
            r"|Bearer\s+[A-Za-z0-9_\-.]{20,}"
            r")",
        ),
    ),
    # Email addresses
    (
        "[REDACTED_EMAIL]",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+(?:\.[A-Za-z0-9\-]{2,})+"),
    ),
]


def redact_payload(text: str) -> tuple[str, list[str]]:
    """Mask sensitive data in *text* using detect-secrets + pattern masking.

    Returns:
        (sanitised_text, redaction_types_applied)
    """
    if not text:
        return text, []

    redaction_types: list[str] = []
    sanitised = text

    # Pass 1: detect-secrets scanning to capture detected secret categories
    try:
        with transient_settings(_DETECT_SECRETS_CONFIG):
            for secret in scan_line(sanitised):
                stype = secret.type
                if "JSON Web Token" in stype and "[REDACTED_JWT]" not in redaction_types:
                    redaction_types.append("[REDACTED_JWT]")
                elif any(k in stype for k in ("Key", "Token")) and "[REDACTED_API_KEY]" not in redaction_types:
                    redaction_types.append("[REDACTED_API_KEY]")
                elif "Basic Auth" in stype and "[REDACTED_DB_URI]" not in redaction_types:
                    redaction_types.append("[REDACTED_DB_URI]")
    except Exception:
        pass

    # Pass 2: deterministic pattern substitution
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
