"""Sensitive data redaction using Microsoft Presidio (analyzer + anonymizer)."""

from functools import lru_cache
from typing import Any

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

_BUILTIN_ENTITIES: tuple[str, ...] = (
    "EMAIL_ADDRESS",
    "IP_ADDRESS",
    "CREDIT_CARD",
)

_CUSTOM_SECRET_PATTERNS: dict[str, str] = {
    "JWT": r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]+)?=*",
    "DB_URI": (
        r"(?:jdbc:(?:[a-zA-Z0-9_+.-]+:)?|jdbc:)?"
        r"(?:postgresql|postgres|mysql|mariadb|oracle|sqlserver|"
        r"mongodb(?:\+srv)?|redis|rediss|sqlite)://[^\s,;\"')}\]]+"
        r"|jdbc:[a-zA-Z0-9_+.-]+:[^\s,;\"')}\]]+"
    ),
    "API_KEY": (
        r"(?:sk-[A-Za-z0-9_-]{16,}"
        r"|(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{16,}"
        r"|sk_[A-Za-z0-9_]{16,}"
        r"|gsk_[A-Za-z0-9_-]{16,}"
        r"|(?:ghp_|gho_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{16,}"
        r"|AKIA[A-Z0-9]{16}"
        r"|xox[bpsa]-[A-Za-z0-9-]{20,}"
        r"|Bearer\s+[A-Za-z0-9_\-.]{20,})"
    ),
    "PRIVATE_KEY": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
}

_TARGET_ENTITIES: list[str] = [*_BUILTIN_ENTITIES, *_CUSTOM_SECRET_PATTERNS.keys()]


@lru_cache(maxsize=1)
def _get_engines() -> tuple[AnalyzerEngine, AnonymizerEngine]:
    """Initialize and cache Presidio engines using the lightweight en_core_web_sm model."""
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])

    for entity, regex in _CUSTOM_SECRET_PATTERNS.items():
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity=entity,
                patterns=[Pattern(name=f"{entity.lower()}_pattern", regex=regex, score=0.95)],
            )
        )

    return analyzer, AnonymizerEngine()


def redact_payload(text: str) -> tuple[str, list[str]]:
    """Mask PII and developer secrets in *text*. Returns (sanitised_text, redaction_types)."""
    if not text:
        return text, []

    analyzer, anonymizer = _get_engines()
    results = analyzer.analyze(text=text, entities=_TARGET_ENTITIES, language="en")
    if not results:
        return text, []

    operators = {
        r.entity_type: OperatorConfig("replace", {"new_value": f"[REDACTED_{r.entity_type}]"})
        for r in results
    }
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
    types_applied = sorted({f"[REDACTED_{item.entity_type}]" for item in anonymized.items})
    return anonymized.text, types_applied


def redact_dict(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Recursively redact all string values in *payload*. Returns (sanitised_dict, redaction_types)."""
    applied: set[str] = set()

    def _walk(val: Any) -> Any:
        if isinstance(val, str):
            sanitised, types = redact_payload(val)
            applied.update(types)
            return sanitised
        if isinstance(val, dict):
            return {k: _walk(v) for k, v in val.items()}
        if isinstance(val, list):
            return [_walk(v) for v in val]
        if isinstance(val, tuple):
            return tuple(_walk(v) for v in val)
        return val

    return {k: _walk(v) for k, v in payload.items()}, sorted(applied)
