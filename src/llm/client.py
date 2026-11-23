"""The two ways agents call an LLM: `structured()` for one answer, `tool_agent()` for a tool loop.

Both end in a Pydantic model; LLM replies are never parsed as free text.
"""

import inspect
import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from src.config.settings import get_settings, load_yaml
from src.llm.providers import LLMError, complete
from src.llm.run_context import current_usage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Tool = Callable[[Any], str]  # takes one Pydantic args model, returns text for the LLM

FINISH = "finish"
MALFORMED_TOOL_CALL = ("tool_use_failed", "output_parse_failed")  # Groq rejects these server-side


def structured(role: str, schema: type[T], **variables: Any) -> T:
    """One LLM call that must return `schema`. An invalid reply gets one retry with the error shown."""
    messages = render_prompt(role, **variables)
    messages[0]["content"] += json_instructions(schema)
    for _ in range(2):
        reply = complete(role, messages, response_format={"type": "json_object"})
        try:
            return schema.model_validate_json(reply.content or "")
        except ValidationError as exc:
            error = exc
            messages += [
                {"role": "assistant", "content": reply.content or ""},
                {"role": "user", "content": f"That JSON was invalid:\n{exc}\nReturn only the corrected JSON."},
            ]
    raise LLMError(f"{role}: no valid {schema.__name__} after a retry: {error}")


def tool_agent(role: str, tools: dict[str, Tool], output: type[T], max_steps: int, **variables: Any) -> T:
    """Call tools until the model calls `finish` with an `output` object.

    The model only sees the tools it is given, so each agent's tool restriction is enforced here.
    After `max_steps` only `finish` is offered, with one extra attempt to fix a bad answer.
    """
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
                messages,
                tools=[finish_spec] if final else [*tool_specs, finish_spec],
                tool_choice={"type": "function", "function": {"name": FINISH}} if final else "required",
            )
        except litellm.BadRequestError as exc:
            if not any(code in str(exc) for code in MALFORMED_TOOL_CALL):
                raise
            log.warning("%s made a malformed tool call: %s", role, str(exc)[:300])
            messages.append({"role": "user", "content": f"Your last tool call was rejected:\n{str(exc)[:500]}"})
            continue
        if not message.tool_calls:
            log.warning("%s answered without calling a tool", role)
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


def run_tool(tools: dict[str, Tool], name: str, arguments: str) -> str:
    """Errors go back to the model as text so it can correct itself."""
    if name not in tools:
        return f"error: unknown tool {name!r}; available: {', '.join(tools)}"
    if usage := current_usage.get():
        usage.record_tool_call()
    try:
        result = tools[name](args_model(tools[name]).model_validate_json(arguments or "{}"))
    except Exception as exc:
        result = f"error: {exc}"
    limit = get_settings().tool_output_chars
    return result if len(result) <= limit else result[:limit] + "\n... (truncated)"


def render_prompt(role: str, **variables: Any) -> list[dict]:
    prompt = load_yaml("prompts.yaml")[role]
    return [
        {"role": "system", "content": prompt["system"].strip()},
        {"role": "user", "content": prompt["user"].format(**variables).strip()},
    ]


def json_instructions(schema: type[BaseModel]) -> str:
    """JSON mode guarantees JSON, not the shape, so the schema goes in the prompt."""
    return f"\n\nReply with only a JSON object matching this JSON schema:\n{json.dumps(schema.model_json_schema())}"


def args_model(tool: Tool) -> type[BaseModel]:
    return next(iter(inspect.signature(tool).parameters.values())).annotation


def tool_spec(name: str, tool: Tool) -> dict:
    return function_spec(name, inspect.getdoc(tool) or name, args_model(tool))


def function_spec(name: str, description: str, parameters: type[BaseModel]) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters.model_json_schema()},
    }


def assistant_message(message: Any) -> dict:
    """Only the fields every provider accepts back in the conversation."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in message.tool_calls
        ],
    }
