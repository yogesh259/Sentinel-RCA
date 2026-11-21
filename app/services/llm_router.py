"""LLM router — litellm SDK routing to Groq without silent simulation fallback on rate limits.

Two tiers:
  - Triage: groq/llama-3.1-8b-instant (dedup, fallback trace parsing).
  - Deep RCA: groq/llama-3.3-70b-versatile (git-diff correlation & root cause).
  - Rate Limits: Raises LLMRateLimitError on 429 / litellm.RateLimitError.
  - Configuration: Raises LLMConfigurationError if GROQ_API_KEY is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from app.core.config import settings

logger = logging.getLogger("sentinel-rca.llm_router")

# ── Model identifiers ────────────────────────────────────────────

TRIAGE_MODEL = "groq/llama-3.1-8b-instant"
DEEP_RCA_MODEL = "groq/llama-3.3-70b-versatile"

# ── Domain Exceptions ─────────────────────────────────────────────


class LLMError(Exception):
    """Base domain exception for LLM errors."""


class LLMRateLimitError(LLMError):
    """Raised when upstream LLM provider rejects requests due to rate limits (HTTP 429)."""


class LLMConfigurationError(LLMError):
    """Raised when required LLM configuration (such as GROQ_API_KEY) is missing."""


# ── Cost estimates (USD per 1M tokens) ────────────────────────────

_COST_TABLE: dict[str, dict[str, float]] = {
    TRIAGE_MODEL: {"input": 0.05, "output": 0.08},
    DEEP_RCA_MODEL: {"input": 0.59, "output": 0.79},
    "simulation": {"input": 0.0, "output": 0.0},
}


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

        rates = _COST_TABLE.get(model_key, {"input": 0.0, "output": 0.0})
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


# ── Router ────────────────────────────────────────────────────────

Tier = Literal["triage", "deep_rca"]
Mode = Literal["LIVE_GROQ", "SIMULATION"]


def _get_groq_api_key() -> str:
    """Return raw string Groq API key."""
    if hasattr(settings.groq_api_key, "get_secret_value"):
        return settings.groq_api_key.get_secret_value()
    return str(settings.groq_api_key)


async def llm_complete(
    tier: Tier,
    messages: list[dict[str, str]],
) -> tuple[str, Mode]:
    """Route an LLM completion request to Groq via litellm.

    Raises:
        LLMConfigurationError: If GROQ_API_KEY is missing.
        LLMRateLimitError: If Groq upstream returns a 429 or rate limit error.
    """
    api_key = _get_groq_api_key()
    if not api_key:
        logger.error("LLM call [%s] failed: GROQ_API_KEY is not configured.", tier)
        raise LLMConfigurationError(
            "GROQ_API_KEY is not configured. Live LLM analysis cannot proceed without a valid API key."
        )

    model = TRIAGE_MODEL if tier == "triage" else DEEP_RCA_MODEL

    try:
        import litellm

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.0,
            api_key=api_key,
        )
        content = response.choices[0].message.content or ""
        logger.info("LLM call [%s]: %s returned %d chars", tier, model, len(content))
        return content, "LIVE_GROQ"
    except litellm.RateLimitError as exc:
        logger.warning("Groq rate limit hit for model %s: %s", model, exc)
        raise LLMRateLimitError(
            f"Groq upstream rate limit exceeded for {model}: {exc}"
        ) from exc
    except Exception as exc:
        exc_str = str(exc).lower()
        if "rate limit" in exc_str or "429" in exc_str or getattr(exc, "status_code", None) == 429:
            logger.warning("Groq rate limit hit for model %s: %s", model, exc)
            raise LLMRateLimitError(
                f"Groq upstream rate limit exceeded for {model}: {exc}"
            ) from exc
        logger.error("Groq call failed (%s): %s", model, exc, exc_info=True)
        raise


def get_model_key(tier: Tier) -> str:
    """Return the cost-table key for the given tier."""
    return TRIAGE_MODEL if tier == "triage" else DEEP_RCA_MODEL
