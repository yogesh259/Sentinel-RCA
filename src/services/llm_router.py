"""LLM router — multi-model LiteLLM routing with automatic Groq -> Gemini fallback.

Tiers and routing:
  - Triage: Defaults to groq/llama-3.1-8b-instant.
  - Deep RCA: Defaults to primary_llm_model (groq/llama-3.3-70b-versatile).
  - Fallback: Automatically routes to fallback_llm_model (gemini/gemini-2.5-flash) if primary
    fails due to rate limits (HTTP 429), quota limits, or timeouts.
  - Configuration: Raises LLMConfigurationError if neither GROQ_API_KEY nor GEMINI_API_KEY is configured.
"""


import logging
from dataclasses import dataclass, field
from typing import Literal

from src.core.config import config

logger = logging.getLogger("sentinel-rca.llm_router")

# ── Model identifiers & Cost table (USD per 1M tokens) ─────────────

TRIAGE_MODEL = "groq/llama-3.1-8b-instant"

_COST_TABLE: dict[str, dict[str, float]] = {
    "groq/llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    "groq/llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "gemini/gemini-2.5-flash": {"input": 0.075, "output": 0.30},
}

# ── Domain Exceptions ─────────────────────────────────────────────


class LLMError(Exception):
    """Base domain exception for LLM errors."""


class LLMRateLimitError(LLMError):
    """Raised when upstream LLM providers reject requests due to rate limits or quota exhaustion."""


class LLMConfigurationError(LLMError):
    """Raised when required LLM configuration (such as API keys) is missing."""


# ── Cost tracker ──────────────────────────────────────────────────


@dataclass
class CostTracker:
    """Lightweight per-run token & cost accumulator."""

    tokens_used: int = 0
    cost_usd: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def record(
        self,
        model_key: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        total = input_tokens + output_tokens
        self.tokens_used += total

        rates = _COST_TABLE.get(model_key, {"input": 0.10, "output": 0.30})
        cost = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
        self.cost_usd += cost

        self.calls.append(
            {
                "model": model_key,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 6),
            }
        )

    @property
    def summary(self) -> str:
        return f"${self.cost_usd:.4f} ({self.tokens_used} tokens, {len(self.calls)} calls)"


Tier = Literal["triage", "deep_rca"]


def _get_key_str(secret) -> str:
    if hasattr(secret, "get_secret_value"):
        return secret.get_secret_value()
    return str(secret or "")


def _get_api_key_for_model(model: str) -> str:
    if model.startswith("gemini/") or "gemini" in model.lower():
        return _get_key_str(config.gemini_api_key)
    return _get_key_str(config.groq_api_key)


def get_model_key(tier: Tier) -> str:
    """Return the primary model identifier for the given tier."""
    return TRIAGE_MODEL if tier == "triage" else config.primary_llm_model


async def _execute_completion(model: str, messages: list[dict[str, str]], api_key: str) -> str:
    import litellm

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        temperature=0.0,
        api_key=api_key,
    )
    return response.choices[0].message.content or ""


def _is_rate_or_quota_limit(exc: Exception) -> bool:
    exc_str = str(exc).lower()
    return (
        "rate limit" in exc_str
        or "429" in exc_str
        or "quota" in exc_str
        or "resource_exhausted" in exc_str
        or getattr(exc, "status_code", None) == 429
    )


async def llm_complete(
    tier: Tier,
    messages: list[dict[str, str]],
) -> tuple[str, str]:
    """Route completion to primary model with automatic fallback to fallback_llm_model.

    Returns:
        tuple[str, str]: (completion_content, model_served)
    Raises:
        LLMConfigurationError: If neither GROQ_API_KEY nor GEMINI_API_KEY is configured.
        LLMRateLimitError: If both primary and fallback fail due to rate/quota limits.
    """
    groq_key = _get_key_str(config.groq_api_key)
    gemini_key = _get_key_str(config.gemini_api_key)

    if not groq_key and not gemini_key:
        raise LLMConfigurationError(
            "Neither GROQ_API_KEY nor GEMINI_API_KEY is configured. "
            "Live LLM analysis requires at least one valid provider key."
        )

    primary_model = get_model_key(tier)
    primary_key = _get_api_key_for_model(primary_model)

    fallback_model = config.fallback_llm_model
    fallback_key = _get_api_key_for_model(fallback_model)

    # 1. Attempt Primary Provider
    if primary_key:
        try:
            content = await _execute_completion(primary_model, messages, primary_key)
            logger.info(
                "LLM call [%s]: Primary %s responded successfully (%d chars)",
                tier,
                primary_model,
                len(content),
            )
            return content, primary_model
        except Exception as exc:
            rate_limit = _is_rate_or_quota_limit(exc)
            logger.warning(
                "Primary model %s failed (rate_limit=%s): %s. Triggering fallback to %s...",
                primary_model,
                rate_limit,
                exc,
                fallback_model,
            )
            if not fallback_key:
                if rate_limit:
                    raise LLMRateLimitError(
                        f"Primary model {primary_model} rate limited and GEMINI_API_KEY not configured: {exc}"
                    ) from exc
                raise
    else:
        logger.info(
            "Primary key missing for %s; proceeding directly to fallback %s",
            primary_model,
            fallback_model,
        )

    # 2. Attempt Fallback Provider (Gemini)
    if not fallback_key:
        raise LLMConfigurationError(
            f"Fallback to {fallback_model} failed: GEMINI_API_KEY is not configured."
        )

    try:
        content = await _execute_completion(fallback_model, messages, fallback_key)
        logger.info(
            "LLM call [%s]: Fallback %s responded successfully (%d chars)",
            tier,
            fallback_model,
            len(content),
        )
        return content, fallback_model
    except Exception as exc:
        if _is_rate_or_quota_limit(exc):
            raise LLMRateLimitError(
                f"Both primary ({primary_model}) and fallback ({fallback_model}) exhausted rate limits: {exc}"
            ) from exc
        logger.error("Fallback call to %s failed: %s", fallback_model, exc, exc_info=True)
        raise
