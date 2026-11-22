"""Adapter from inbound webhook payloads (Sentry or generic) to one stack-trace text.

Both the gateway (for the fingerprint) and the parse_trace node (for the analysis)
need the trace, so the format knowledge lives here once. Parsing itself is always
done by trace_mcp, reached through the MCP client like every other tool.
"""

from pydantic import BaseModel

from src.schemas.mcp.trace import ParsedStackTrace
from src.services.mcp_client import call_tool

GENERIC_PAYLOAD_KEYS = ("service", "error", "stack_trace")


class IncidentSummary(BaseModel):
    """What the gateway needs from a payload: fingerprint inputs and an issue title."""

    exception_type: str
    file_name: str
    line_number: int
    title: str


def is_generic_payload(payload: dict) -> bool:
    """Generic webhooks send {service, error, stack_trace}; anything else is Sentry."""
    return any(key in payload for key in GENERIC_PAYLOAD_KEYS)


def sentry_event(payload: dict) -> dict:
    """Sentry nests the event under data.event on some webhooks and directly in data on others."""
    data = payload.get("data", {})
    return data.get("event", data)


def sentry_payload_to_trace(payload: dict) -> str:
    """Rebuild a JVM-style trace from a Sentry webhook so one parser handles both formats."""
    event = sentry_event(payload)
    values = event.get("exception", {}).get("values", [])
    if not values:
        return ""

    top = values[0]
    lines = [f"{top.get('type', 'UnknownException')}: {top.get('value', '')}"]
    culprit = event.get("culprit", "")
    # Sentry lists frames outermost-first; JVM traces print deepest first.
    for frame in reversed(top.get("stacktrace", {}).get("frames", [])):
        func = frame.get("function", "?")
        if culprit and "." not in func and culprit.endswith(func):
            func = culprit
        lines.append(f"\tat {func}({frame.get('filename', '?')}:{frame.get('lineno', 0)})")
    return "\n".join(lines)


def payload_to_trace(payload: dict) -> str:
    """Return the raw stack-trace text carried by either payload format."""
    if is_generic_payload(payload):
        return payload.get("stack_trace", "")
    return sentry_payload_to_trace(payload)


def incident_title(payload: dict, parsed: ParsedStackTrace) -> str:
    """Human-readable title: the sender's own title if given, else derived from the parse."""
    if is_generic_payload(payload):
        service = payload.get("service", "service")
        return f"{service}: {payload.get('error', 'Error')}"
    data = payload.get("data", {})
    title = data.get("title") or sentry_event(payload).get("title", "")
    return title or f"{parsed.exception_type} in {parsed.target_file or 'unknown'}:{parsed.line_number}"


async def summarise_incident(payload: dict) -> IncidentSummary:
    """Parse the payload's trace via trace_mcp and extract the fingerprint inputs."""
    trace = payload_to_trace(payload)
    if trace:
        parsed = await call_tool("trace", "parse_stack_trace", {"raw_trace": trace}, ParsedStackTrace)
    else:
        parsed = ParsedStackTrace(exception_type="UnknownException")

    exception_type = parsed.exception_type
    # A generic payload without a parsable header still names its error explicitly.
    if exception_type == "UnknownException" and payload.get("error"):
        exception_type = payload["error"]

    return IncidentSummary(
        exception_type=exception_type,
        file_name=parsed.target_file or "unknown",
        line_number=parsed.line_number,
        title=incident_title(payload, parsed),
    )


__all__ = [
    "IncidentSummary",
    "payload_to_trace",
    "sentry_payload_to_trace",
    "summarise_incident",
]
