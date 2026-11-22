from pathlib import Path

from src.agents.context import describe_plan, describe_review, describe_trace
from src.llm.client import tool_agent
from src.models.finding import Finding
from src.models.state import SentinelState
from src.tools.repo_tools import RepoTools

MAX_STEPS = 8


def locator_node(state: SentinelState) -> dict:
    """Read the code around the focus frames. On a retry it also sees what the reviewer rejected."""
    tools = RepoTools(Path(state["repo_path"]))
    finding = tool_agent(
        "locator",
        {"read_file": tools.read_file, "search_code": tools.search_code, "list_dir": tools.list_dir},
        Finding,
        MAX_STEPS,
        trace=describe_trace(state["trace"]),
        plan=describe_plan(state["trace"], state["plan"]),
        review_problems=describe_review(state["review"]),
    )
    update: dict = {"findings": [finding.model_copy(update={"source": "locator"})]}
    if state["review"] is not None:  # only the reviewer sends the run back here
        update["retries"] = state["retries"] + 1
    return update
