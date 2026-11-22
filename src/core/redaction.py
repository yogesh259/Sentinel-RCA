"""Deterministic secret/PII redaction, applied before any LLM call or DB write.

Built on Yelp's detect-secrets (pattern + keyword plugins: JWT, AWS, GitHub, Stripe,
Slack, basic-auth URIs, private keys, `password = "..."`, ...) plus a few explicit
patterns it does not cover (emails, bearer tokens, LLM API keys).

The high-entropy plugins are disabled on purpose: on stack traces and source code they
flag ordinary identifiers (`OrderController`, commit SHAs) and would destroy the very
evidence the RCA needs.
"""

import re
from functools import lru_cache
from typing import Any

from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class
from detect_secrets.core.scan import scan_line
from detect_secrets.settings import transient_settings

_NOISY_PLUGINS = {"Base64HighEntropyString", "HexHighEntropyString"}

# Gaps in detect-secrets' plugin set.
_EXTRA_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL_ADDRESS": re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    "BEARER_TOKEN": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    "LLM_API_KEY": re.compile(r"\b(?:gsk|sk(?:-proj)?|sk-ant)[-_][A-Za-z0-9_-]{20,}"),
}

# A finding is masked together with the surrounding token, so a key the plugin matched
# only partially (e.g. the first 24 chars of a Stripe key) is never half-leaked.
_TOKEN = r"[^\s\"'`<>(){}\[\],;]*"


@lru_cache(maxsize=1)
def _plugin_settings() -> dict:
    """detect-secrets configuration: every built-in plugin except the entropy ones."""
    plugins = get_mapping_from_secret_type_to_class().values()
    return {
        "plugins_used": [
            {"name": p.__name__} for p in plugins if p.__name__ not in _NOISY_PLUGINS
        ]
    }


def _label(secret_type: str) -> str:
    """`JSON Web Token` -> `[REDACTED_JSON_WEB_TOKEN]`."""
    slug = re.sub(r"\W+", "_", secret_type).strip("_").upper()
    return f"[REDACTED_{slug}]"


def _redact_line(line: str, labels: set[str]) -> str:
    """Mask every detect-secrets finding on one line."""
    for secret in scan_line(line):
        value = secret.secret_value
        if not value or value not in line:
            continue
        label = _label(secret.type)
        line = re.sub(_TOKEN + re.escape(value) + _TOKEN, label, line)
        labels.add(label)
    return line


def redact_payload(text: str) -> tuple[str, list[str]]:
    """Mask secrets and PII in text. Returns (sanitized_text, redaction_labels)."""
    if not text:
        return text, []

    labels: set[str] = set()
    with transient_settings(_plugin_settings()):
        text = "\n".join(_redact_line(line, labels) for line in text.split("\n"))

    for name, pattern in _EXTRA_PATTERNS.items():
        text, count = pattern.subn(f"[REDACTED_{name}]", text)
        if count:
            labels.add(f"[REDACTED_{name}]")
    return text, sorted(labels)


def redact_dict(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Recursively redact strings in a nested dictionary or collection."""
    applied_redactions: set[str] = set()

    def redact_value(value: Any) -> Any:
        if isinstance(value, str):
            sanitized, redaction_types = redact_payload(value)
            applied_redactions.update(redaction_types)
            return sanitized
        if isinstance(value, dict):
            return {key: redact_value(val) for key, val in value.items()}
        if isinstance(value, list):
            return [redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact_value(item) for item in value)
        return value

    sanitized_payload = {key: redact_value(val) for key, val in payload.items()}
    return sanitized_payload, sorted(applied_redactions)
