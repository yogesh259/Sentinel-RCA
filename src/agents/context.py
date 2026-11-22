"""Turns state objects into the plain text the prompts show the LLM."""

from src.models.finding import Finding
from src.models.plan import Plan
from src.models.report import ReviewResult
from src.models.trace import ParsedTrace

MAX_FRAMES_SHOWN = 30


def describe_trace(trace: ParsedTrace) -> str:
    """Exception, cause chain and numbered frames; [user] marks frames in this repository."""
    lines = [f"Exception: {trace.exception_type}: {trace.message}"]
    lines += [f"Caused by: {cause}" for cause in trace.caused_by]
    lines.append("Frames (top of stack first):")
    for index, frame in enumerate(trace.frames[:MAX_FRAMES_SHOWN]):
        owner = "user" if frame.is_user_code else "external"
        lines.append(f"  {index}. [{owner}] {frame.file}:{frame.line} in {frame.function}")
    return "\n".join(lines)


def describe_plan(trace: ParsedTrace, plan: Plan) -> str:
    """The focus frames spelled out as file:line, plus the hypotheses and checks."""
    focus = [
        f"  - {trace.frames[i].file}:{trace.frames[i].line} in {trace.frames[i].function}" for i in plan.focus_frames
    ]
    return "\n".join(
        [
            "Focus frames:",
            *focus,
            "Hypotheses:",
            *[f"  - {h}" for h in plan.hypotheses],
            "Look for:",
            *[f"  - {item}" for item in plan.look_for],
        ]
    )


def latest_findings(findings: list[Finding]) -> list[Finding]:
    """The newest finding per agent; a locator retry replaces its first attempt."""
    return list({finding.source: finding for finding in findings}.values())


def describe_findings(findings: list[Finding]) -> str:
    return "\n\n".join(finding.model_dump_json(indent=2) for finding in latest_findings(findings))


def describe_review(review: ReviewResult | None) -> str:
    """Reviewer problems from the previous attempt, or nothing on the first pass."""
    if review is None or review.grounded:
        return ""
    return "A reviewer rejected the previous report. Fix these problems:\n" + "\n".join(
        f"- {problem}" for problem in review.problems
    )
