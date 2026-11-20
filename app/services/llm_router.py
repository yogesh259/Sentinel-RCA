"""LLM router — litellm SDK routing to Groq with Simulation fallback.

Two tiers:
  - Triage: groq/llama-3.1-8b-instant (dedup, fallback trace parsing).
  - Deep RCA: groq/llama-3.3-70b-versatile (git-diff correlation & root cause).
  - Fallback: Deterministic Simulation Mode when GROQ_API_KEY is missing or rate-limited.
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

# Deterministic simulation response for when Groq is unavailable
_SIMULATION_RESPONSE = (
    "[SIMULATION] Deterministic analysis: stack trace parsed, "
    "exception correlated with recent commits. No live LLM was called."
)


def _should_simulate() -> bool:
    """Return True if we must fall back to Simulation Mode."""
    return not settings.groq_api_key


async def llm_complete(
    tier: Tier,
    messages: list[dict[str, str]],
    *,
    force_simulate: bool = False,
) -> tuple[str, Mode]:
    """Route an LLM completion request to Groq via litellm or Simulation.

    Returns:
        (response_text, mode) where mode is "LIVE_GROQ" or "SIMULATION".
    """
    if force_simulate or _should_simulate():
        logger.info("LLM call [%s]: Simulation Mode (no GROQ_API_KEY or rate-limited)", tier)
        return _SIMULATION_RESPONSE, "SIMULATION"

    model = TRIAGE_MODEL if tier == "triage" else DEEP_RCA_MODEL

    try:
        import litellm

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.0,
            api_key=settings.groq_api_key,
        )
        content = response.choices[0].message.content or ""
        logger.info("LLM call [%s]: %s returned %d chars", tier, model, len(content))
        return content, "LIVE_GROQ"
    except Exception as exc:
        logger.warning("Groq call failed (%s), falling back to Simulation Mode: %s", model, exc)
        return _SIMULATION_RESPONSE, "SIMULATION"


def get_model_key(tier: Tier) -> str:
    """Return the cost-table key for the given tier."""
    if _should_simulate():
        return "simulation"
    return TRIAGE_MODEL if tier == "triage" else DEEP_RCA_MODEL
