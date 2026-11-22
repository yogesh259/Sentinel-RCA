"""The comparison point for the evals: one agent, every tool, one prompt, same output schema."""

from src.llm.client import tool_agent
from src.models.report import RCAReport
from src.services import repo_cache
from src.tools.git_tools import GitTools
from src.tools.repo_tools import RepoTools

MAX_STEPS = 13  # the graph's locator (8) + history (5) budget, so neither side gets more tool calls


def run_baseline(repo_url: str, raw_trace: str, base_commit: str | None = None) -> RCAReport:
    repo = repo_cache.prepare(repo_url, base_commit)
    code, history = RepoTools(repo), GitTools(repo)
    tools = {
        "read_file": code.read_file,
        "search_code": code.search_code,
        "list_dir": code.list_dir,
        "recent_commits": history.recent_commits,
        "blame_lines": history.blame_lines,
        "show_commit": history.show_commit,
    }
    return tool_agent("baseline", tools, RCAReport, MAX_STEPS, raw_trace=raw_trace)
