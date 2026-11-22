"""Every branch in the graph. Pure functions of state, so each decision is easy to test and explain."""

from langgraph.graph import END

from src.models.state import SentinelState

MAX_RETRIES = 1


def route_after_prepare(state: SentinelState) -> str:
    return "fail" if state["error"] else "parse_trace"


def route_after_parse(state: SentinelState) -> str:
    if state["error"]:
        return "fail"
    has_user_code = any(frame.is_user_code for frame in state["trace"].frames)
    return "orchestrator" if has_user_code else "report_external"


def route_after_review(state: SentinelState) -> str:
    """One locator retry when the reviewer finds ungrounded claims; then a human decides."""
    if not state["review"].grounded and state["retries"] < MAX_RETRIES:
        return "locator"
    return "human_review"


def route_after_decision(state: SentinelState) -> str:
    return "create_pr" if state["decision"].action == "approve" else END
