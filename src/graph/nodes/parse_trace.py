"""Parse trace node: redaction + trace_mcp parsing."""

import logging

from src.core.redaction import redact_dict
from src.graph.state import RCAState
from src.mcp_servers.git_mcp import resolve_victim_file
from src.mcp_servers.trace_mcp import parse_stack_trace

logger = logging.getLogger("sentinel-rca.nodes.parse_trace")


async def parse_trace_node(state: RCAState) -> dict:
    """Node 1: Parse raw stack trace via trace_mcp (runs redaction first)."""
    payload = state.get("raw_payload", {})
    dict_redactions: list[str] = []
    if payload:
        payload, dict_redactions = redact_dict(payload)

    raw = state.get("raw_trace", "")
    if not raw:
        event = payload.get("data", {}).get("event", payload.get("data", {}))
        exc_info = event.get("exception", {})
        values = exc_info.get("values", [])
        if values:
            top = values[0]
            exc_type = top.get("type", "UnknownException")
            exc_val = top.get("value", "")
            frames = top.get("stacktrace", {}).get("frames", [])
            lines = [f"{exc_type}: {exc_val}"]
            culprit = event.get("culprit", "")
            for f in frames:
                fn = f.get("filename", "?")
                ln = f.get("lineno", 0)
                func = f.get("function", "?")
                if culprit and "." not in func and culprit.endswith(func):
                    func = culprit
                lines.append(f"\tat {func}({fn}:{ln})")
            raw = "\n".join(lines)

    parsed = parse_stack_trace(raw)
    target_file = parsed.target_file
    line_number = parsed.line_number
    class_name = parsed.class_name
    method_name = parsed.method_name

    # Fallback to checking payload frames directly if parsing left target_file empty
    if not target_file or target_file in ("?", "unknown"):
        event = payload.get("data", {}).get("event", payload.get("data", {}))
        exc_info = event.get("exception", {})
        values = exc_info.get("values", [])
        if values:
            frames = values[0].get("stacktrace", {}).get("frames", [])
            for f in reversed(frames):
                fn = f.get("filename")
                if fn and fn not in ("?", "unknown"):
                    target_file = fn
                    if not line_number and f.get("lineno"):
                        try:
                            line_number = int(f["lineno"])
                        except (ValueError, TypeError):
                            pass
                    if not method_name and f.get("function"):
                        method_name = str(f["function"])
                    if not class_name and fn.endswith(".java"):
                        class_name = fn.removesuffix(".java")
                    break

    resolved_file = resolve_victim_file(target_file) if target_file else ""
    final_target = resolved_file or target_file
    current_steps = state.get("step_count", 0)

    combined_redactions = list(parsed.redaction_types)
    for dr in dict_redactions:
        if dr not in combined_redactions:
            combined_redactions.append(dr)

    return {
        "raw_payload": payload,
        "raw_trace": raw,
        "exception_type": parsed.exception_type,
        "exception_message": parsed.exception_message,
        "target_file": final_target,
        "class_name": class_name,
        "method_name": method_name,
        "line_number": line_number,
        "redaction_types": combined_redactions,
        "step_count": current_steps + 1,
    }


__all__ = ["parse_trace_node"]
