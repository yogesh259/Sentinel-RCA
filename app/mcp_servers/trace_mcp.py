"""trace_mcp — Standalone MCP server for stack-trace parsing.

Exposes:
    parse_stack_trace(raw_trace: str) -> ParsedStackTrace

Runs PII/secret redaction first, then extracts structured exception info.
"""

from __future__ import annotations

import re

from app.core.mcp_schemas import ParsedStackTrace
from app.core.redaction import redact_payload

# ── Java stack-trace patterns ─────────────────────────────────────

# Matches lines like:
#   at com.victim.controller.OrderController.createOrder(OrderController.java:34)
_JAVA_FRAME_RE = re.compile(
    r"at\s+"
    r"(?P<class_name>[\w$.]+)\."           # fully-qualified class
    r"(?P<method>[\w<>$]+)"                 # method name
    r"\((?P<file>[\w.]+):(?P<line>\d+)\)"   # (FileName.java:34)
)

# Matches the exception header:
#   java.lang.NullPointerException: Cannot invoke String.length() ...
#   NullPointerException: message
_JAVA_EXCEPTION_RE = re.compile(
    r"(?:[\w$.]*\.)?(?P<type>\w+(?:Exception|Error|Throwable))"
    r"(?::\s*(?P<message>.+))?"
)

# ── Python traceback patterns ────────────────────────────────────

# Matches:  File "foo.py", line 42, in some_func
_PY_FRAME_RE = re.compile(
    r'File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<func>\S+)'
)

# Matches:  ValueError: something went wrong
_PY_EXCEPTION_RE = re.compile(
    r"^(?P<type>\w+(?:Error|Exception|Warning)):\s*(?P<message>.+)",
    re.MULTILINE,
)


def parse_stack_trace(raw_trace: str) -> ParsedStackTrace:
    """Parse a raw stack trace into a structured ``ParsedStackTrace``.

    1. Redacts PII/secrets from the raw text first.
    2. Attempts Java-style parsing; falls back to Python-style.
    3. Extracts the *deepest application frame* (last match).
    """
    # ── Step 1: Redact ────────────────────────────────────────────
    sanitised, redaction_types = redact_payload(raw_trace)

    # ── Step 2: Try Java ──────────────────────────────────────────
    java_frames = list(_JAVA_FRAME_RE.finditer(sanitised))
    java_exc = _JAVA_EXCEPTION_RE.search(sanitised)

    if java_frames:
        # Use the last (deepest / most-recent) frame
        last = java_frames[-1]
        exc_type = java_exc.group("type") if java_exc else "UnknownException"
        exc_msg = (java_exc.group("message") or "") if java_exc else ""

        return ParsedStackTrace(
            exception_type=exc_type,
            exception_message=exc_msg.strip(),
            target_file=last.group("file"),
            class_name=last.group("class_name"),
            method_name=last.group("method"),
            line_number=int(last.group("line")),
            redaction_types=redaction_types,
        )

    # ── Step 3: Try Python ────────────────────────────────────────
    py_frames = list(_PY_FRAME_RE.finditer(sanitised))
    py_exc = _PY_EXCEPTION_RE.search(sanitised)

    if py_frames:
        last = py_frames[-1]
        exc_type = py_exc.group("type") if py_exc else "UnknownException"
        exc_msg = (py_exc.group("message") or "") if py_exc else ""

        file_name = last.group("file")
        # Derive class_name from file path: app/services/foo.py → app.services.foo
        class_name = file_name.replace("/", ".").removesuffix(".py")

        return ParsedStackTrace(
            exception_type=exc_type,
            exception_message=exc_msg.strip(),
            target_file=file_name,
            class_name=class_name,
            method_name=last.group("func"),
            line_number=int(last.group("line")),
            redaction_types=redaction_types,
        )

    # ── Fallback: extract what we can ─────────────────────────────
    exc_type = "UnknownException"
    exc_msg = ""
    if java_exc:
        exc_type = java_exc.group("type")
        exc_msg = (java_exc.group("message") or "").strip()
    elif py_exc:
        exc_type = py_exc.group("type")
        exc_msg = (py_exc.group("message") or "").strip()

    return ParsedStackTrace(
        exception_type=exc_type,
        exception_message=exc_msg,
        redaction_types=redaction_types,
    )
