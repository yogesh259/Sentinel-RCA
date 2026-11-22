"""Semantic dedup node: tracker_mcp candidate search + triage-tier LLM judgement.

Redis fingerprinting at the gateway already drops exact repeats for free. This node
catches the harder case: a *different* trace (new line number, reworded message) that
is the same underlying bug as an issue already open.
"""

import logging

from src.graph.state import RCAState
from src.schemas.mcp.tracker import IssueRecord
from src.schemas.rca import DedupVerdict
from src.services import llm_router
from src.services.mcp_client import call_tool
from src.services.rca_prompts import build_dedup_messages

logger = logging.getLogger("sentinel-rca.nodes.dedup")


async def judge_duplicate(state: RCAState, issues: list[dict]) -> tuple[DedupVerdict, list[dict]]:
    """Ask the triage model whether the incident matches one of the candidate issues.

    An id the model invents (not among the candidates) is treated as "not a duplicate".
    """
    messages = build_dedup_messages(
        state.get("exception_type", ""),
        state.get("exception_message", ""),
        state.get("target_file", ""),
        issues,
    )
    verdict, calls = await llm_router.complete_structured("triage", messages, DedupVerdict)
    if verdict.duplicate_of not in {i["id"] for i in issues}:
        verdict = verdict.model_copy(update={"duplicate_of": None})
    return verdict, [c.usage() for c in calls]


async def dedup_node(state: RCAState) -> dict:
    """Node 2: flag the incident as a duplicate of an open issue, if it is one."""
    exception_type = state.get("exception_type", "")
    target_file = state.get("target_file", "")

    try:
        found = await call_tool(
            "tracker",
            "search_existing_issues",
            {"exception_type": exception_type, "file_name": target_file},
            list[IssueRecord],
        )
        issues = [i.model_dump(mode="json") for i in found]
    except Exception as exc:
        logger.warning("tracker_mcp search failed: %s", exc)
        issues = []

    verdict, llm_calls = DedupVerdict(reason="No open issue shares this exception or file."), []
    if issues:
        try:
            verdict, llm_calls = await judge_duplicate(state, issues)
        except llm_router.LLMError as exc:
            # Failing open costs at most one duplicate ticket; failing closed could hide a new bug.
            logger.warning("Triage dedup unavailable, treating as new: %s", exc)
            verdict = DedupVerdict(reason=f"Dedup judgement unavailable: {exc}")

    is_duplicate = verdict.duplicate_of is not None
    summary = (
        f"Duplicate of issue #{verdict.duplicate_of}: {verdict.reason}"
        if is_duplicate
        else f"New incident ({len(issues)} candidate issue(s) checked). {verdict.reason}"
    )
    return {
        "is_duplicate": is_duplicate,
        "dedup_summary": summary,
        "llm_calls": llm_calls,
    }
