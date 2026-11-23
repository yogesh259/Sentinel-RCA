from pathlib import Path

from src.agents.context import describe_plan, describe_trace
from src.llm.client import tool_agent
from src.models.finding import Finding
from src.models.state import SentinelState
from src.tools.git_tools import GitTools

MAX_STEPS = 8


def history_node(state: SentinelState) -> dict:
    """Look for the commit that introduced the failing code (blame, log, diffs)."""
    tools = GitTools(Path(state["repo_path"]))
    finding = tool_agent(
        "history",
        {
            "recent_commits": tools.recent_commits,
            "blame_lines": tools.blame_lines,
            "show_commit": tools.show_commit,
        },
        Finding,
        MAX_STEPS,
        trace=describe_trace(state["trace"]),
        plan=describe_plan(state["trace"], state["plan"]),
    )
    return {"findings": [finding.model_copy(update={"source": "history"})]}
