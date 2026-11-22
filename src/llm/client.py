"""Every LLM call goes through here: `structured()` for one answer, `tool_agent()` for a tool loop.

Both always end in a Pydantic model; free text from the LLM is never parsed by hand.
"""

import inspect
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from src.config.settings import get_settings, load_yaml, model_for

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Tool = Callable[[Any], str]  # takes one Pydantic args model, returns text for the LLM

RATE_LIMIT_WAITS = (10, 20, 40, 60)  # free tiers cap tokens per minute; waiting refills the budget
FINISH = "finish"
MODEL_MISTAKES = ("tool_use_failed", "output_parse_failed")  # Groq error codes for a malformed tool call
TOOL_MISUSE = "Your last tool call was rejected. Use only the listed tools, and `finish` to answer."

litellm.suppress_debug_info = True


class LLMError(Exception):
    """The model could not produce a valid answer."""


@dataclass
class Usage:
    """Tokens, cost and call counts for one run. Locator and history run in parallel, hence the lock."""

    llm_calls: int = 0
    tool_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, model: str, response: Any) -> None:
        with self.lock:
            self.llm_calls += 1
            self.tokens += response.usage.total_tokens if response.usage else 0
            self.cost_usd += response_cost(response)
            self.models.add(model)

    def record_tool_call(self) -> None:
        with self.lock:
            self.tool_calls += 1


# The Usage of the run in progress. LangGraph copies the context into each node's thread,
# so every node of one run records into the same object, and concurrent runs never mix.
current_usage: ContextVar[Usage | None] = ContextVar("current_usage", default=None)


@contextmanager
def track_usage() -> Iterator[Usage]:
    """Wrap one graph run (or resume) to collect its tokens and cost."""
    usage = Usage()
    token = current_usage.set(usage)
    try:
        yield usage
    finally:
        current_usage.reset(token)


def structured(role: str, schema: type[T], **variables: Any) -> T:
    """One LLM call that must return `schema`; on a validation error, retry once with the error shown."""
    messages = render_prompt(role, **variables)
    messages[0]["content"] += json_instructions(schema)
    model = model_for(role)
    reply = complete(role, model, messages, response_format={"type": "json_object"})
    try:
        return schema.model_validate_json(reply.content or "")
    except ValidationError as exc:
        messages += [
            {"role": "assistant", "content": reply.content or ""},
            {"role": "user", "content": f"That JSON was invalid:\n{exc}\nReturn only the corrected JSON."},
        ]
        retry = complete(role, model, messages, response_format={"type": "json_object"})
        try:
            return schema.model_validate_json(retry.content or "")
        except ValidationError as exc:
            raise LLMError(f"{role}: invalid {schema.__name__} after one retry: {exc}") from exc


def tool_agent(role: str, tools: dict[str, Tool], output: type[T], max_steps: int, **variables: Any) -> T:
    """A small tool-calling loop that ends when the model calls `finish` with an `output` object.

    The agent can only call the tools it is given, so tool restriction is enforced here, not by the
    prompt. After `max_steps` only `finish` is offered, with one extra attempt to fix a bad answer.
    """
    model = model_for(role)
    messages = render_prompt(role, **variables)
    tool_specs = [tool_spec(name, fn) for name, fn in tools.items()]
    finish_spec = function_spec(FINISH, "Submit your final answer.", output)
    previous_call = None

    for step in range(max_steps + 2):
        final = step >= max_steps
        if step == max_steps:
            messages.append({"role": "user", "content": "Step limit reached. Call `finish` now."})
        try:
            message = complete(
                role,
                model,
                messages,
                tools=[finish_spec] if final else [*tool_specs, finish_spec],
                tool_choice={"type": "function", "function": {"name": FINISH}} if final else "required",
            )
        except litellm.BadRequestError as exc:
            if not any(code in str(exc) for code in MODEL_MISTAKES):
                raise
            # Groq validates tool calls server-side and rejects bad ones; let the model try again.
            messages.append({"role": "user", "content": f"{TOOL_MISUSE}\n{str(exc)[:500]}"})
            continue
        if not message.tool_calls:
            messages.append({"role": "assistant", "content": message.content or ""})
            messages.append({"role": "user", "content": "Call a tool, or `finish` with your answer."})
            continue

        messages.append(assistant_message(message))
        for call in message.tool_calls:
            name, arguments = call.function.name, call.function.arguments
            if name == FINISH:
                try:
                    return output.model_validate_json(arguments)
                except ValidationError as exc:
                    log.warning("%s gave an invalid finish: %s", role, exc)
                    result = f"error: that answer is invalid, fix it and call finish again:\n{exc}"
            elif (name, arguments) == previous_call:
                result = "blocked: identical to your previous call; use the result you already have."
            else:
                result = run_tool(tools, name, arguments)
            previous_call = (name, arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "name": name, "content": result})

    raise LLMError(f"{role}: no valid `finish` call within {max_steps} steps")


def complete(role: str, model: str, messages: list[dict], **kwargs: Any) -> Any:
    """One raw LiteLLM call, retried on rate limits, recorded in the run's Usage. Returns the message."""
    for wait in (*RATE_LIMIT_WAITS, None):
        try:
            response = litellm.completion(
                model=model, messages=messages, temperature=0, api_key=api_key_for(model), **kwargs
            )
            break
        except litellm.RateLimitError as exc:
            if wait is None or "per day" in str(exc):  # a daily quota won't refill by waiting
                raise LLMError(f"{model} is rate limited: {exc}") from exc
            log.warning("%s rate limited on %s; retrying in %ss", role, model, wait)
            time.sleep(wait)

    tokens = response.usage.total_tokens if response.usage else 0
    log.info("%s -> %s: %d tokens, $%.4f", role, model, tokens, response_cost(response))
    if usage := current_usage.get():
        usage.record(model, response)
    return response.choices[0].message


def run_tool(tools: dict[str, Tool], name: str, arguments: str) -> str:
    """Run one tool call. Errors go back to the LLM as text so it can correct itself; nothing raises."""
    if name not in tools:
        return f"error: unknown tool {name!r}; available: {', '.join(tools)}"
    if usage := current_usage.get():
        usage.record_tool_call()
    try:
        args = args_model(tools[name]).model_validate_json(arguments or "{}")
        result = tools[name](args)
    except Exception as exc:
        result = f"error: {exc}"
    limit = get_settings().tool_output_chars
    return result if len(result) <= limit else result[:limit] + "\n... (truncated)"


def render_prompt(role: str, **variables: Any) -> list[dict]:
    """System + user messages for a role, from prompts.yaml."""
    prompt = load_yaml("prompts.yaml")[role]
    return [
        {"role": "system", "content": prompt["system"].strip()},
        {"role": "user", "content": prompt["user"].format(**variables).strip()},
    ]


def json_instructions(schema: type[BaseModel]) -> str:
    """Tells the model the exact JSON shape to reply with (JSON mode alone doesn't enforce a schema)."""
    return f"\n\nReply with only a JSON object matching this JSON schema:\n{json.dumps(schema.model_json_schema())}"


def args_model(tool: Tool) -> type[BaseModel]:
    """A tool's single parameter is its Pydantic args model."""
    return next(iter(inspect.signature(tool).parameters.values())).annotation


def tool_spec(name: str, tool: Tool) -> dict:
    """OpenAI-style tool definition (LiteLLM translates it per provider) from the tool's docstring + args model."""
    return function_spec(name, inspect.getdoc(tool) or name, args_model(tool))


def function_spec(name: str, description: str, parameters: type[BaseModel]) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters.model_json_schema()},
    }


def assistant_message(message: Any) -> dict:
    """Rebuild the assistant turn with only the fields every provider accepts back."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in message.tool_calls
        ],
    }


def api_key_for(model: str) -> str | None:
    """Pass the key explicitly so it never has to live in os.environ."""
    settings = get_settings()
    key = {"groq": settings.groq_api_key, "gemini": settings.gemini_api_key}.get(model.split("/")[0])
    return key.get_secret_value() if key else None


def response_cost(response: Any) -> float:
    """USD cost from LiteLLM's price table; 0 when the model isn't priced there."""
    try:
        return float(litellm.completion_cost(completion_response=response) or 0)
    except Exception:
        return 0.0
