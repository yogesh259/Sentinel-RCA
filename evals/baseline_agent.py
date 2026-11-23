"""The comparison point for the evals: one agent, every tool, one prompt, same output schema."""

from src.llm.client import tool_agent
from src.models.report import RCAReport
from src.services import repo
from src.tools.git_tools import GitTools
from src.tools.repo_tools import RepoTools

MAX_STEPS = 14  # locator (8) + history (6), the same tool budget as the graph


def run_baseline(repo_url: str, raw_trace: str, base_commit: str | None = None) -> RCAReport:
    checkout = repo.clone(repo_url, base_commit)
    code, history = RepoTools(checkout), GitTools(checkout)
    tools = {
        "read_file": code.read_file,
        "search_code": code.search_code,
        "list_dir": code.list_dir,
        "recent_commits": history.recent_commits,
        "blame_lines": history.blame_lines,
        "show_commit": history.show_commit,
    }
    return tool_agent("baseline", tools, RCAReport, MAX_STEPS, raw_trace=raw_trace)
