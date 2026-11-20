"""Hybrid LLM router with per-run cost tracking.

Supports USE_LOCAL_OLLAMA=true (Ollama) | false (Gemini Flash / GPT-4o-mini).
Two tiers:
  - Triage: cheap/fast model for dedup + initial classification.
  - Deep RCA: frontier model for git-diff correlation on genuinely new bugs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.language_models import BaseChatModel

from app.core.config import settings

logger = logging.getLogger("sentinel-rca.llm_router")

# ── Cost estimates (USD per 1M tokens) ────────────────────────────
_COST_TABLE: dict[str, dict[str, float]] = {
    # model_key -> {input, output} cost per 1M tokens
    "ollama": {"input": 0.0, "output": 0.0},  # local = free
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.0},
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


def get_llm(tier: Tier) -> BaseChatModel:
    """Return the appropriate LLM for *tier*, respecting USE_LOCAL_OLLAMA.

    Triage tier:
      - USE_LOCAL_OLLAMA=true  → Ollama (local, free)
      - USE_LOCAL_OLLAMA=false → Gemini Flash / GPT-4o-mini (cheap hosted)

    Deep RCA tier:
      - Frontier model (Gemini Flash / Pro or GPT-4o)
    """
    if tier == "triage":
        if settings.use_local_ollama:
            from langchain_ollama import ChatOllama

            logger.info("Triage LLM: Ollama (%s)", settings.ollama_triage_model)
            return ChatOllama(
                model=settings.ollama_triage_model,
                base_url=settings.ollama_base_url,
                temperature=0.0,
            )
        else:
            if settings.openai_api_key and "gpt" in settings.hosted_triage_model:
                from langchain_openai import ChatOpenAI

                logger.info("Triage LLM: OpenAI (%s)", settings.hosted_triage_model)
                return ChatOpenAI(
                    model=settings.hosted_triage_model,
                    api_key=settings.openai_api_key,
                    temperature=0.0,
                )
            from langchain_google_genai import ChatGoogleGenerativeAI

            logger.info("Triage LLM: Gemini (%s)", settings.hosted_triage_model)
            return ChatGoogleGenerativeAI(
                model=settings.hosted_triage_model,
                google_api_key=settings.google_api_key or None,
                temperature=0.0,
            )

    elif tier == "deep_rca":
        if settings.openai_api_key and "gpt" in settings.deep_rca_model:
            from langchain_openai import ChatOpenAI

            logger.info("Deep-RCA LLM: OpenAI (%s)", settings.deep_rca_model)
            return ChatOpenAI(
                model=settings.deep_rca_model,
                api_key=settings.openai_api_key,
                temperature=0.0,
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        logger.info("Deep-RCA LLM: Gemini (%s)", settings.deep_rca_model)
        return ChatGoogleGenerativeAI(
            model=settings.deep_rca_model,
            google_api_key=settings.google_api_key or None,
            temperature=0.0,
        )

    raise ValueError(f"Unknown LLM tier: {tier!r}")


def get_model_key(tier: Tier) -> str:
    """Return the cost-table key for the given tier."""
    if tier == "triage":
        return "ollama" if settings.use_local_ollama else settings.hosted_triage_model
    return settings.deep_rca_model
