from pathlib import Path

from src.agents.context import describe_findings, describe_plan, describe_review, describe_trace, latest_findings
from src.llm.client import structured
from src.models.finding import Finding
from src.models.report import DraftReport, RCAReport
from src.models.state import SentinelState
from src.services.repo import build_patch, line_history

MAX_HISTORY_RANGES = 3


def rca_writer_node(state: SentinelState) -> dict:
    """Combine the locator and history findings into one report with an optional patch."""
    draft = structured(
        "rca_writer",
        DraftReport,
        trace=describe_trace(state["trace"]),
        plan=describe_plan(state["trace"], state["plan"]),
        findings=describe_findings(state["findings"]),
        line_history=cited_line_history(Path(state["repo_path"]), state["findings"]),
        review_problems=describe_review(state["review"]),
    )
    patch = build_patch(Path(state["repo_path"]), draft.fix) if draft.fix else None
    return {"report": RCAReport(**draft.model_dump(exclude={"fix"}), suggested_patch=patch)}


def cited_line_history(repo: Path, findings: list[Finding]) -> str:
    """The history agent blames the trace's lines; this covers the lines the locator actually cited,
    which may be in another file or a config file the trace never mentions."""
    cited = [
        (e.file, e.start_line, e.end_line)
        for f in latest_findings(findings)
        if f.source == "locator"
        for e in f.evidence
    ]
    parts = [
        f"{file}:{start}-{end}\n{history}"
        for file, start, end in list(dict.fromkeys(cited))[:MAX_HISTORY_RANGES]
        if (history := line_history(repo, file, start, end))
    ]
    return "\n\n".join(parts) or "No history found for the cited lines."
