from src.agents.context import describe_findings, describe_plan, describe_review, describe_trace
from src.llm.client import structured
from src.models.report import RCAReport
from src.models.state import SentinelState


def rca_writer_node(state: SentinelState) -> dict:
    """Combine the locator and history findings into one report with an optional patch."""
    report = structured(
        "rca_writer",
        RCAReport,
        trace=describe_trace(state["trace"]),
        plan=describe_plan(state["trace"], state["plan"]),
        findings=describe_findings(state["findings"]),
        review_problems=describe_review(state["review"]),
    )
    return {"report": report}
