"""LLM router — two-tier LiteLLM routing with Groq primary and Gemini fallback.

Tiers:
  - triage:   groq/llama-3.1-8b-instant — cheap calls (dedup, unknown-trace extraction).
  - deep_rca: PRIMARY_LLM_MODEL (groq/llama-3.3-70b-versatile) — root-cause judgement.

Resilience: if the primary provider fails (typically a free-tier 429) the call is retried
once on FALLBACK_LLM_MODEL. When no provider can serve the call a domain exception is
raised; there is no silent simulation mode.
"""


import json
import logging
from dataclasses import asdict, dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from src.core.config import config

logger = logging.getLogger("sentinel-rca.llm_router")

Tier = Literal["triage", "deep_rca"]
TRIAGE_MODEL = "groq/llama-3.1-8b-instant"

# USD per 1M tokens; unknown models are costed conservatively.
_COST_TABLE: dict[str, dict[str, float]] = {
    "groq/llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    "groq/llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "gemini/gemini-2.5-flash": {"input": 0.30, "output": 2.50},
}
_DEFAULT_RATES = {"input": 1.0, "output": 3.0}

ModelT = TypeVar("ModelT", bound=BaseModel)


# ── Domain exceptions ─────────────────────────────────────────────


class LLMError(Exception):
    """Base domain exception for LLM errors."""


class LLMRateLimitError(LLMError):
    """Raised when every configured provider rejects the call for rate/quota limits."""


class LLMConfigurationError(LLMError):
    """Raised when required LLM configuration (such as API keys) is missing."""


class LLMOutputError(LLMError):
    """Raised when the model's reply cannot be validated against the expected schema."""


# ── Result type ───────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMResult:
    """One completed LLM call, with the usage needed for the per-incident cost audit."""

    content: str
    model: str
    tier: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def usage(self) -> dict:
        """Usage record stored on the graph state (content omitted to keep traces small)."""
        record = asdict(self)
        record.pop("content")
        return record


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Price a call from the static cost table."""
    rates = _COST_TABLE.get(model, _DEFAULT_RATES)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def get_model_key(tier: Tier) -> str:
    """Return the primary model identifier for the given tier."""
    return TRIAGE_MODEL if tier == "triage" else config.primary_llm_model


def _api_key_for_model(model: str) -> str:
    """Pick the provider key matching a LiteLLM model id."""
    secret = config.gemini_api_key if model.startswith("gemini/") else config.groq_api_key
    return secret.get_secret_value()


def _is_rate_or_quota_limit(exc: Exception) -> bool:
    """Detect provider throttling across LiteLLM exception types and raw HTTP errors."""
    text = str(exc).lower()
    return getattr(exc, "status_code", None) == 429 or any(
        marker in text for marker in ("rate limit", "429", "quota", "resource_exhausted")
    )


# ── Completion ────────────────────────────────────────────────────


async def _execute_completion(
    model: str, tier: str, messages: list[dict[str, str]], api_key: str, json_mode: bool
) -> LLMResult:
    """Call one provider via LiteLLM and capture token usage."""
    import litellm

    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    response = await litellm.acompletion(
        model=model, messages=messages, temperature=0.0, api_key=api_key, **kwargs
    )
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "completion_tokens", 0) or 0
    return LLMResult(
        content=response.choices[0].message.content or "",
        model=model,
        tier=tier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimate_cost(model, input_tokens, output_tokens),
    )


async def llm_complete(
    tier: Tier, messages: list[dict[str, str]], json_mode: bool = False
) -> LLMResult:
    """Complete on the tier's primary model, falling back to FALLBACK_LLM_MODEL on failure.

    Raises:
        LLMConfigurationError: no provider key is configured.
        LLMRateLimitError: every available provider is rate/quota limited.
    """
    routes = [
        (model, key)
        for model in (get_model_key(tier), config.fallback_llm_model)
        if (key := _api_key_for_model(model))
    ]
    if not routes:
        raise LLMConfigurationError(
            "Neither GROQ_API_KEY nor GEMINI_API_KEY is configured; live analysis is unavailable."
        )

    last_exc: Exception | None = None
    for model, key in routes:
        try:
            result = await _execute_completion(model, tier, messages, key, json_mode)
            logger.info(
                "LLM [%s] %s: %d in / %d out tokens",
                tier, model, result.input_tokens, result.output_tokens,
            )
            return result
        except Exception as exc:
            logger.warning("LLM [%s] %s failed: %s", tier, model, exc)
            last_exc = exc

    if last_exc is not None and _is_rate_or_quota_limit(last_exc):
        raise LLMRateLimitError(f"All LLM providers are rate limited: {last_exc}") from last_exc
    raise LLMError(f"All LLM providers failed: {last_exc}") from last_exc


# ── Structured output ─────────────────────────────────────────────


def _strip_code_fence(text: str) -> str:
    """Remove a ```json fence some models wrap around JSON despite instructions."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else ""
        cleaned = cleaned.removesuffix("```").strip()
    return cleaned


async def complete_structured(
    tier: Tier, messages: list[dict[str, str]], model_cls: type[ModelT]
) -> tuple[ModelT, list[LLMResult]]:
    """Return a validated *model_cls* instance plus every call made to obtain it.

    On invalid output the model gets exactly one repair attempt that includes the
    validation error; a second failure raises LLMOutputError rather than guessing.
    """
    calls: list[LLMResult] = []
    conversation = list(messages)
    for attempt in range(2):
        result = await llm_complete(tier, conversation, json_mode=True)
        calls.append(result)
        try:
            return model_cls.model_validate_json(_strip_code_fence(result.content)), calls
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning("Invalid %s output (attempt %d): %s", model_cls.__name__, attempt + 1, exc)
            conversation = [
                *messages,
                {"role": "assistant", "content": result.content},
                {
                    "role": "user",
                    "content": f"Your reply failed validation:\n{exc}\nReturn only corrected JSON.",
                },
            ]
    raise LLMOutputError(f"{model_cls.__name__} output failed validation twice.")
