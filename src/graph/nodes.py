"""Graph nodes that are plain code (no LLM)."""

import logging
from pathlib import Path

from github import GithubException
from langgraph.types import interrupt

from src.config.settings import get_settings
from src.models.report import Decision, RCAReport
from src.models.state import SentinelState
from src.services import repo_cache
from src.services.pull_request import open_draft_pr, pr_body
from src.tools.trace_parser import TraceParseError, mark_user_code, parse_trace

log = logging.getLogger(__name__)
EXTERNAL_FRAMES_SHOWN = 5


def prepare_repo_node(state: SentinelState) -> dict:
    """Clone or update the repo and check out the commit to analyse."""
    try:
        return {"repo_path": str(repo_cache.prepare(state["repo_url"], state["base_commit"]))}
    except repo_cache.RepoError as exc:
        return {"error": f"Could not prepare the repository: {exc}"}


def parse_trace_node(state: SentinelState) -> dict:
    """Parse the trace and mark which frames are files in this repo."""
    try:
        trace = parse_trace(state["raw_trace"])
    except TraceParseError as exc:
        return {"error": str(exc)}
    return {"trace": mark_user_code(trace, repo_cache.list_files(Path(state["repo_path"])))}


def report_external_node(state: SentinelState) -> dict:
    """No frame is in the repo, so this is a library or infrastructure error. No LLM needed."""
    trace = state["trace"]
    top = "\n".join(f"- {frame.raw}" for frame in trace.frames[:EXTERNAL_FRAMES_SHOWN])
    report = RCAReport(
        title=f"{trace.exception_type} raised outside this repository",
        root_cause=(
            "No frame in the stack trace belongs to this repository, so the error was raised inside a "
            f"library, framework or the runtime. Check configuration, dependency versions and inputs. "
            f"Top frames:\n{top}"
        ),
        suspects=[],
        introduced_by=None,
        suggested_patch=None,
        confidence=0.0,
    )
    return {"report": report}


def human_review_node(state: SentinelState) -> dict:
    """Pause the run until a person approves or rejects. The resume value is a Decision dict."""
    review = state["review"]
    answer = interrupt({"report": state["report"].model_dump(), "review": review.model_dump() if review else None})
    return {"decision": Decision.model_validate(answer)}


def create_pr_node(state: SentinelState) -> dict:
    """Open a draft PR for an approved patch. Dry run (and the public demo) never writes to GitHub."""
    report = state["report"]
    if not report.suggested_patch or not get_settings().opens_prs:
        return {"pr_url": None}
    body = pr_body(report, state["decision"])
    try:
        return {"pr_url": open_draft_pr(Path(state["repo_path"]), state["repo_url"], report, body)}
    except (repo_cache.RepoError, GithubException) as exc:
        return {"error": f"Could not open the PR: {exc}"}


def fail_node(state: SentinelState) -> dict:
    """End the run; the error stays in state for the CLI/UI to show."""
    log.error("run failed: %s", state["error"])
    return {}
