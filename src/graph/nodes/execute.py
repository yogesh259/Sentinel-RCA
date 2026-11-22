"""Execute node: carries out the approved action through tracker_mcp."""

import logging

from src.graph.state import RCAState, WorkflowStatus
from src.schemas.mcp.tracker import IssueRecord, PRPreview
from src.services.mcp_client import call_tool

logger = logging.getLogger("sentinel-rca.nodes.execute")


async def perform_action(state: RCAState, proposal: dict) -> dict:
    """Run one approved action and return what it produced."""
    action = proposal["action"]

    if action == "deduplicate":
        return {"action": action, "message": state.get("dedup_summary", "Incident deduplicated.")}

    if action == "create_issue":
        issue_args = {
            "title": f"{state.get('exception_type', 'Issue')}: {proposal['root_cause'][:80]}",
            "root_cause": proposal["root_cause"],
            "commit_sha": proposal["commit_sha"],
            "severity": proposal["severity"],
            "exception_type": state.get("exception_type", ""),
            "file_name": state.get("target_file", ""),
        }
        issue = await call_tool("tracker", "create_issue", issue_args, IssueRecord)
        return {"action": action, "issue": issue.model_dump(mode="json")}

    if action == "draft_hotfix_pr":
        pr_args = {
            "commit_sha": proposal["commit_sha"],
            "file_path": proposal["file_path"],
            "patch_summary": proposal["patch_summary"] or "Auto-generated hotfix patch",
        }
        pr = await call_tool("tracker", "draft_hotfix_pr", pr_args, PRPreview)
        return {"action": action, "pr": pr.model_dump(mode="json")}

    raise ValueError(f"No executor for action '{action}'")


async def execute_node(state: RCAState) -> dict:
    """Node 5: run the approved action, or record why nothing was run."""
    status = state.get("status")
    proposal = state.get("proposal", {})

    if status == WorkflowStatus.ESCALATED:
        return {"result": {"action": "escalate", "reason": state.get("auth_reason", "")}}

    if status == WorkflowStatus.REJECTED:
        reviewer = (state.get("hitl_response") or {}).get("reviewer_role", "unknown")
        return {"result": {"action": proposal.get("action"), "decision": "rejected", "reviewer": reviewer}}

    try:
        result = await perform_action(state, proposal)
    except Exception as exc:
        logger.error("Executing %s failed: %s", proposal.get("action"), exc)
        return {"status": WorkflowStatus.ERROR, "result": {"action": proposal.get("action"), "error": str(exc)}}

    return {"status": WorkflowStatus.COMPLETED, "result": result}
