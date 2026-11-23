"""Sends a chat request to the first model of a role that can serve it.

Each role lists models in models.yaml. A model that is out of quota, overloaded, too slow or has a
rejected key is skipped for the next one; only the last model in the list waits and retries.
"""

import logging
import time
from typing import Any

import litellm
from langgraph.config import get_stream_writer

from src.config.settings import get_settings, models_for
from src.llm.run_context import current_usage, visitor_keys

log = logging.getLogger(__name__)

RETRY_WAITS = (10, 20, 40, 60)
REQUEST_TIMEOUT_S = 45
TEMPORARY_ERRORS = (
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
    litellm.Timeout,
)

litellm.suppress_debug_info = True
litellm.drop_params = True  # drop request params a provider doesn't support, e.g. temperature on Gemini 3


class LLMError(Exception):
    """No model could produce a valid answer."""


def complete(role: str, messages: list[dict], **kwargs: Any) -> Any:
    """One chat completion for a role, falling back through its models. Returns the reply message."""
    models = [model for model in models_for(role) if api_key_for(model)]
    if not models:
        raise LLMError(f"{role}: no API key for any of its models. Set GEMINI_API_KEY or GROQ_API_KEY.")

    for model, next_model in zip(models, [*models[1:], None], strict=True):
        try:
            response = call_model(role, model, messages, has_fallback=next_model is not None, **kwargs)
        except LLMError as exc:
            if next_model is None:
                raise
            notify(role, f"{exc}; trying {next_model}")
            continue
        record(role, model, response)
        return response.choices[0].message


def call_model(role: str, model: str, messages: list[dict], has_fallback: bool, **kwargs: Any) -> Any:
    waits = iter(RETRY_WAITS)
    while True:
        try:
            return litellm.completion(
                model=model,
                messages=messages,
                temperature=0,
                api_key=api_key_for(model),
                timeout=REQUEST_TIMEOUT_S,
                **kwargs,
            )
        except litellm.AuthenticationError as exc:
            raise LLMError(f"{model}: the API key was rejected") from exc
        except TEMPORARY_ERRORS as exc:
            wait = None if has_fallback or is_daily_quota(exc) else next(waits, None)
            if wait is None:
                raise LLMError(f"{model} {why_unavailable(exc)}") from exc
            notify(role, f"{model} {why_unavailable(exc)}; retrying in {wait}s")
            time.sleep(wait)


def api_key_for(model: str) -> str | None:
    """The visitor's key for this provider if they gave one, else the server's."""
    provider = model.split("/")[0]
    if key := (visitor_keys.get() or {}).get(provider):
        return key
    settings = get_settings()
    server_key = {"groq": settings.groq_api_key, "gemini": settings.gemini_api_key}.get(provider)
    return server_key.get_secret_value() if server_key else None


def record(role: str, model: str, response: Any) -> None:
    tokens = response.usage.total_tokens if response.usage else 0
    cost = response_cost(response)
    log.info("%s -> %s: %d tokens, $%.4f", role, model, tokens, cost)
    if usage := current_usage.get():
        usage.record_llm_call(model, tokens, cost)


def response_cost(response: Any) -> float:
    """USD cost from LiteLLM's price table; 0 for models it doesn't price."""
    try:
        return float(litellm.completion_cost(completion_response=response) or 0)
    except Exception:
        return 0.0


def is_daily_quota(exc: Exception) -> bool:
    """Groq says "per day", Gemini "PerDay". Waiting won't help with either."""
    return "perday" in str(exc).lower().replace(" ", "")


def why_unavailable(exc: Exception) -> str:
    if is_daily_quota(exc):
        return "has used up today's free quota"
    if isinstance(exc, litellm.RateLimitError):
        return "is rate limited"
    if isinstance(exc, litellm.Timeout):
        return "is too slow right now"
    return "is overloaded"


def notify(role: str, text: str) -> None:
    """Log a retry or fallback and stream it to the UI, so a wait is never silent."""
    log.warning("%s: %s", role, text)
    try:
        get_stream_writer()({"role": role, "notice": text})
    except RuntimeError:
        pass  # not inside a graph run (e.g. the eval baseline); the log line is enough
