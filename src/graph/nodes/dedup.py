"""Check for duplicate issues using tracker_mcp."""

import logging

from src.graph.state import RCAState
from src.mcp_servers import tracker_mcp

logger = logging.getLogger("sentinel-rca.nodes.dedup")


async def dedup_node(state: RCAState) -> dict:
    """Find existing issues matching this exception and target file."""
    exception_type = state.get("exception_type", "")
    target_file = state.get("target_file", "")
    current_steps = state.get("step_count", 0)

    try:
        matches = await tracker_mcp.search_existing_issues(exception_type, target_file)
        issues = [m.model_dump() for m in matches]
    except Exception as ex:
        logger.warning("tracker_mcp search failed: %s", ex)
        issues = []

    is_duplicate = bool(issues)
    summary = f"Found {len(issues)} existing issue(s) for {exception_type} in {target_file}."
    if is_duplicate:
        summary += " Marked as duplicate."

    return {
        "is_duplicate": is_duplicate,
        "existing_issues": issues,
        "dedup_summary": summary,
        "triage_summary": summary,
        "step_count": current_steps + 1,
    }
