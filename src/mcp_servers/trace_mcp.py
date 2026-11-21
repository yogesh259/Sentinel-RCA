"""trace_mcp — Standalone MCP server for stack-trace parsing.

Exposes:
    parse_stack_trace(raw_trace: str) -> ParsedStackTrace

Runs PII/secret redaction first, then extracts structured exception info.
"""

from __future__ import annotations

import re

from src.core.redaction import redact_payload
from src.schemas.mcp.trace import ParsedStackTrace

# ── Java stack-trace patterns ─────────────────────────────────────

# Matches lines like:
#   at com.victim.controller.OrderController.createOrder(OrderController.java:34)
#   at com.example.order.OrderController.applyDiscount(OrderController.java:42)
#   at createOrder(OrderController.java:34)
#   at (OrderController.java:34)
_JAVA_FRAME_RE = re.compile(
    r"(?:at\s+)?"
    r"(?:(?P<call_target>[\w$.<>]+)\s*)?"
    r"\((?P<file>[\w.\-]+\.(?:java|kt|scala|groovy)|[\w.\-]+):(?P<line>\d+)\)",
    re.IGNORECASE,
)

_JAVA_BARE_FILE_RE = re.compile(
    r"(?P<file>[\w.\-]+\.java)(?::(?P<line>\d+))?",
    re.IGNORECASE,
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
_PY_FRAME_RE = re.compile(r'File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<func>\S+)')

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
        # Prefer application frames over third-party framework lines
        app_frames = [
            f
            for f in java_frames
            if not any(
                fw in f.group(0).lower()
                for fw in ("org.springframework", "org.apache", "java.base", "sun.", "jdk.")
            )
        ]
        selected = app_frames[-1] if app_frames else java_frames[-1]

        exc_type = java_exc.group("type") if java_exc else "UnknownException"
        exc_msg = (java_exc.group("message") or "") if java_exc else ""

        call_target = selected.group("call_target") or ""
        if "." in call_target:
            class_name, method_name = call_target.rsplit(".", 1)
        else:
            class_name, method_name = "", call_target

        target_file = selected.group("file")
        if not class_name and target_file.endswith(".java"):
            class_name = target_file.removesuffix(".java")

        line_num = int(selected.group("line")) if selected.group("line") else 0

        return ParsedStackTrace(
            exception_type=exc_type,
            exception_message=exc_msg.strip(),
            target_file=target_file,
            class_name=class_name,
            method_name=method_name,
            line_number=line_num,
            redaction_types=redaction_types,
        )

    # Check bare Java file fallback
    bare_match = _JAVA_BARE_FILE_RE.search(sanitised)
    if bare_match:
        exc_type = java_exc.group("type") if java_exc else "UnknownException"
        exc_msg = (java_exc.group("message") or "") if java_exc else ""
        target_file = bare_match.group("file")
        line_num = int(bare_match.group("line")) if bare_match.group("line") else 0
        return ParsedStackTrace(
            exception_type=exc_type,
            exception_message=exc_msg.strip(),
            target_file=target_file,
            class_name=target_file.removesuffix(".java"),
            method_name="",
            line_number=line_num,
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
        # Derive class_name from file path: src/services/foo.py → src.services.foo
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
