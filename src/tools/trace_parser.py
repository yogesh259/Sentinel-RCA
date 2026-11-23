"""Turns a raw Java or Python stack trace into structured frames. Plain code, no LLM."""

import re
from pathlib import PurePosixPath

from src.models.trace import Frame, ParsedTrace

# at com.shop.OrderService.place(OrderService.java:42)
JAVA_FRAME = re.compile(r"^\s*at\s+(?P<function>[\w$.<>/]+)\((?P<file>[\w$\-]+\.(?:java|kt|scala)):(?P<line>\d+)\)")
# java.lang.NullPointerException: Cannot invoke "String.length()" ...
JAVA_EXCEPTION = re.compile(r"(?:[\w$]+\.)*(?P<type>[\w$]+(?:Exception|Error|Throwable))(?::\s*(?P<message>.*))?")
JAVA_CAUSED_BY = re.compile(r"^\s*Caused by:\s*(?P<cause>.+)$")

# File "app/services/order.py", line 42, in place_order
PYTHON_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<function>\S+)')
# KeyError: 'customer'   (an exception line starts at column 0)
PYTHON_EXCEPTION = re.compile(r"^(?:\w+\.)*(?P<type>\w+)(?::\s*(?P<message>.*))?$")
PYTHON_TRACEBACK = "Traceback (most recent call last):"
PYTHON_LIBRARIES = ("site-packages", "dist-packages", "/lib/python", "<frozen")


class TraceParseError(ValueError):
    """The text is not a Java or Python stack trace we can read."""


def parse_trace(text: str) -> ParsedTrace:
    """Detect the language and parse. Frames come back top of stack (where it threw) first."""
    if any(JAVA_FRAME.match(line) for line in text.splitlines()):
        return parse_java(text)
    if PYTHON_TRACEBACK in text:
        return parse_python(text)
    raise TraceParseError("No Java or Python stack frames found in the trace.")


def parse_java(text: str) -> ParsedTrace:
    """Java prints the throwing frame first, then each `Caused by:` section below it."""
    lines = text.splitlines()
    first_frame = next(i for i, line in enumerate(lines) if JAVA_FRAME.match(line))
    header = next(
        (m for line in reversed(lines[:first_frame]) if (m := JAVA_EXCEPTION.search(line))),
        None,
    )
    frames = [
        Frame(raw=line.strip(), language="java", file=m["file"], line=int(m["line"]), function=m["function"])
        for line in lines
        if (m := JAVA_FRAME.match(line))
    ]
    causes = [m["cause"].strip() for line in lines if (m := JAVA_CAUSED_BY.match(line))]
    return ParsedTrace(
        language="java",
        exception_type=header["type"] if header else "UnknownException",
        message=(header["message"] or "").strip() if header else "",
        frames=frames,
        caused_by=causes,
    )


def parse_python(text: str) -> ParsedTrace:
    """Python prints the throwing frame last, and a chained cause as an earlier traceback block."""
    blocks = [block for block in text.split(PYTHON_TRACEBACK)[1:] if block.strip()]
    frames: list[Frame] = []
    exceptions: list[tuple[str, str]] = []
    for block in reversed(blocks):  # final exception first, its causes after
        block_frames = [
            Frame(raw=line.strip(), language="python", file=m["file"], line=int(m["line"]), function=m["function"])
            for line in block.splitlines()
            if (m := PYTHON_FRAME.match(line))
        ]
        frames.extend(reversed(block_frames))
        exceptions.append(python_exception(block))

    exception_type, message = exceptions[0]
    causes = [f"{kind}: {msg}" if msg else kind for kind, msg in exceptions[1:]]
    return ParsedTrace(
        language="python", exception_type=exception_type, message=message, frames=frames, caused_by=causes
    )


def python_exception(block: str) -> tuple[str, str]:
    """The `Type: message` line at column 0 that ends one traceback block."""
    for line in block.splitlines():
        if line and not line[0].isspace() and (m := PYTHON_EXCEPTION.match(line)):
            return m["type"], (m["message"] or "").strip()
    return "UnknownException", ""


def mark_user_code(trace: ParsedTrace, repo_files: list[str]) -> ParsedTrace:
    """Point each frame that exists in the repo at its repo path and flag it as user code.

    Library frames (JDK, Spring, site-packages) never match a repo file, so they stay external.
    """
    frames = []
    for frame in trace.frames:
        path = repo_path_for(frame, repo_files)
        frames.append(frame.model_copy(update={"file": path, "is_user_code": True}) if path else frame)
    return trace.model_copy(update={"frames": frames})


def repo_path_for(frame: Frame, repo_files: list[str]) -> str | None:
    """Find the repo file a frame refers to, or None if it is not in the repo."""
    if frame.language == "java":
        return java_repo_path(frame, repo_files)
    if any(lib in frame.file for lib in PYTHON_LIBRARIES):
        return None
    frame_path = PurePosixPath(frame.file.replace("\\", "/"))
    matches = [f for f in repo_files if frame_path.match(f) or PurePosixPath(f).match(str(frame_path))]
    return max(matches, key=len) if matches else None


def java_repo_path(frame: Frame, repo_files: list[str]) -> str | None:
    """`com.shop.OrderService.place` in OrderService.java -> `.../com/shop/OrderService.java`.

    Falls back to the file name alone only when exactly one repo file has that name.
    """
    class_path = (frame.function or "").rpartition(".")[0]
    package = class_path.rpartition(".")[0]
    suffix = "/".join([*package.split("."), frame.file]) if package else frame.file
    if exact := [f for f in repo_files if f == suffix or f.endswith("/" + suffix)]:
        return exact[0]
    same_name = [f for f in repo_files if PurePosixPath(f).name == frame.file]
    return same_name[0] if len(same_name) == 1 else None
