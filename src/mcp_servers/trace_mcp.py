"""trace_mcp — Standalone MCP server for stack-trace parsing.

Exposes:
    parse_stack_trace(raw_trace: str) -> ParsedStackTrace

Runs PII/secret redaction first, then extracts structured exception info and the
ordered list of application frames (deepest first) that git_mcp later blames.
Needs no secrets at all. Run over stdio with `python -m src.mcp_servers.trace_mcp`.
"""


import re

from mcp.server.mcpserver import MCPServer

from src.core.redaction import redact_payload
from src.schemas.mcp.trace import ParsedStackTrace, StackFrame

# ── Java stack-trace patterns ─────────────────────────────────────

# Matches lines like:
#   at com.victim.controller.OrderController.createOrder(OrderController.java:34)
#   at createOrder(OrderController.java:34)
#   at (OrderController.java:34)
_JAVA_FRAME_RE = re.compile(
    r"(?:at\s+)?"
    r"(?:(?P<call_target>[\w$.<>]+)\s*)?"
    # A source extension is required so "(db-host.internal:5432)" in a message isn't a frame.
    r"\((?P<file>[\w.\-]+\.(?:java|kt|scala|groovy)):(?P<line>\d+)\)",
    re.IGNORECASE,
)

_JAVA_BARE_FILE_RE = re.compile(
    r"(?P<file>[\w.\-]+\.java)(?::(?P<line>\d+))?",
    re.IGNORECASE,
)

# Matches the exception header:
#   java.lang.NullPointerException: Cannot invoke String.length() ...
_JAVA_EXCEPTION_RE = re.compile(
    r"(?:[\w$.]*\.)?(?P<type>\w+(?:Exception|Error|Throwable))"
    r"(?::\s*(?P<message>.+))?"
)

# Framework / runtime frames that never belong to the customer's repository.
_JVM_LIBRARY_PREFIXES = (
    "org.springframework",
    "org.apache",
    "java.",
    "javax.",
    "jdk.",
    "sun.",
    "kotlin.",
)

# ── Python traceback patterns ────────────────────────────────────

# Matches:  File "foo.py", line 42, in some_func
_PY_FRAME_RE = re.compile(r'File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<func>\S+)')

# Matches:  ValueError: something went wrong / requests.exceptions.ConnectionError: ...
_PY_EXCEPTION_RE = re.compile(
    r"^(?:[\w.]+\.)?(?P<type>\w+(?:Error|Exception|Warning)):\s*(?P<message>.+)",
    re.MULTILINE,
)

_PY_LIBRARY_MARKERS = ("site-packages", "dist-packages", "/lib/python", "<frozen")


def _java_frame(match: re.Match[str]) -> StackFrame:
    """Convert a Java frame regex match into a StackFrame."""
    call_target = match.group("call_target") or ""
    module, function = call_target.rsplit(".", 1) if "." in call_target else ("", call_target)
    file_name = match.group("file")
    if not module and file_name.endswith(".java"):
        module = file_name.removesuffix(".java")
    return StackFrame(
        file=file_name,
        line=int(match.group("line") or 0),
        function=function,
        module=module,
    )


def _is_jvm_library_frame(frame: StackFrame) -> bool:
    """True for JDK/framework frames, which can't be blamed in the user's repo."""
    return frame.module.startswith(_JVM_LIBRARY_PREFIXES)


def _python_frame(match: re.Match[str]) -> StackFrame:
    """Convert a Python traceback regex match into a StackFrame."""
    file_name = match.group("file")
    return StackFrame(
        file=file_name,
        line=int(match.group("line")),
        function=match.group("func"),
        module=file_name.lstrip("/").replace("/", ".").removesuffix(".py"),
    )


def _exception_header(
    primary: re.Match[str] | None, secondary: re.Match[str] | None = None
) -> tuple[str, str]:
    """Return (type, message) from the first exception-header match available."""
    match = primary or secondary
    if not match:
        return "UnknownException", ""
    return match.group("type"), (match.group("message") or "").strip()


def _build_result(
    exc: tuple[str, str], frames: list[StackFrame], redaction_types: list[str]
) -> ParsedStackTrace:
    """Assemble the parse result; the deepest app frame is the primary target."""
    top = frames[0] if frames else None
    return ParsedStackTrace(
        exception_type=exc[0],
        exception_message=exc[1],
        target_file=top.file if top else "",
        class_name=top.module if top else "",
        method_name=top.function if top else "",
        line_number=top.line if top else 0,
        frames=frames,
        redaction_types=redaction_types,
    )


def parse_stack_trace(raw_trace: str) -> ParsedStackTrace:
    """Parse a raw stack trace into a structured ``ParsedStackTrace``.

    1. Redacts PII/secrets from the raw text first.
    2. Tries JVM-style frames, then a bare ``Foo.java:12`` mention, then Python.
    3. Keeps only application frames, ordered deepest-first.
    """
    sanitised, redaction_types = redact_payload(raw_trace)
    java_exc = _JAVA_EXCEPTION_RE.search(sanitised)
    py_exc = _PY_EXCEPTION_RE.search(sanitised)

    # JVM traces list the deepest frame first.
    java_frames = [_java_frame(m) for m in _JAVA_FRAME_RE.finditer(sanitised)]
    if java_frames:
        app_frames = [f for f in java_frames if not _is_jvm_library_frame(f)] or java_frames
        return _build_result(_exception_header(java_exc), app_frames, redaction_types)

    bare_match = _JAVA_BARE_FILE_RE.search(sanitised)
    if bare_match:
        file_name = bare_match.group("file")
        frame = StackFrame(
            file=file_name,
            line=int(bare_match.group("line") or 0),
            module=file_name.removesuffix(".java"),
        )
        return _build_result(_exception_header(java_exc), [frame], redaction_types)

    # Python tracebacks list the deepest frame last.
    py_frames = [_python_frame(m) for m in _PY_FRAME_RE.finditer(sanitised)]
    if py_frames:
        py_frames.reverse()
        app_frames = [
            f for f in py_frames if not any(m in f.file for m in _PY_LIBRARY_MARKERS)
        ] or py_frames
        return _build_result(_exception_header(py_exc), app_frames, redaction_types)

    return _build_result(_exception_header(java_exc, py_exc), [], redaction_types)


server = MCPServer("trace_mcp")
server.add_tool(parse_stack_trace)


if __name__ == "__main__":
    server.run()
