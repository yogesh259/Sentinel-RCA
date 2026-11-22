"""Investigate git node: git_mcp commits + diff correlation."""

import logging

from src.graph.state import RCAState
from src.mcp_servers.git_mcp import (
    GitInputValidationError,
    get_commit_diff,
    get_recent_commits,
    resolve_victim_file,
)
from src.schemas.mcp.git import CommitInfo

logger = logging.getLogger("sentinel-rca.nodes.investigate")


async def investigate_git_node(state: RCAState) -> dict:
    """Node 3: Investigate git history and correlate diffs."""
    current_steps = state.get("step_count", 0)

    if state.get("is_duplicate"):
        return {
            "root_cause": "Duplicate of previously tracked issue.",
            "suggested_severity": "low",
            "patch_summary": "",
            "step_count": current_steps + 1,
        }

    raw_target_file = state.get("target_file", "")
    target_file = resolve_victim_file(raw_target_file) if raw_target_file else ""
    exception_type = state.get("exception_type", "")
    exception_message = state.get("exception_message", "")

    commits: list[CommitInfo] = []
    if target_file:
        candidate_paths = [target_file]
        if "/" not in target_file:
            candidate_paths.append(f"src/main/java/com/victim/controller/{target_file}")
            candidate_paths.append(f"src/main/java/com/victim/service/{target_file}")

        for cp in candidate_paths:
            try:
                commits = await get_recent_commits(cp, limit=5)
                if commits:
                    target_file = cp
                    break
            except (GitInputValidationError, RuntimeError, FileNotFoundError):
                continue

    suspect_dicts = [c.model_dump() for c in commits]
    root_cause = f"{exception_type} in {target_file}" if target_file else exception_type
    patch_summary = ""
    suggested_severity = state.get("suggested_severity") or "medium"

    if commits:
        try:
            diff = await get_commit_diff(commits[0].sha)
            diff_text = diff.diff

            if "NullPointer" in exception_type or "null" in exception_message.lower():
                suggested_severity = "high"
                root_cause = (
                    f"NullPointerException in {target_file}:{state.get('line_number', 0)}. "
                    f"Likely introduced by commit {commits[0].short_sha} ('{commits[0].message}')."
                )
                patch_summary = f"Add null check for argument in {target_file}"
            elif "leak" in diff_text.lower() or "close()" in diff_text.lower():
                suggested_severity = "high"
                root_cause = (
                    f"Connection/resource leak in {target_file}. "
                    f"Introduced by commit {commits[0].short_sha}."
                )
                patch_summary = f"Ensure connection/stream is closed in {target_file}"
            else:
                root_cause = f"{exception_type} in {target_file} (commit {commits[0].short_sha})"
                patch_summary = f"Resolve {exception_type} in {target_file}"
        except Exception as exc:
            logger.warning("get_commit_diff error: %s", exc)

    return {
        "target_file": target_file,
        "suspect_commits": suspect_dicts,
        "root_cause": root_cause,
        "suggested_severity": suggested_severity,
        "patch_summary": patch_summary,
        "step_count": current_steps + 1,
    }


__all__ = ["investigate_git_node"]
