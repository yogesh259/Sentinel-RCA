"""Execute approved actions against tracker_mcp or finalize terminal state."""

import logging

from src.graph.state import RCAState, WorkflowStatus
from src.schemas.mcp.tracker import IssueRecord, PRPreview
from src.services.mcp_client import call_tool

logger = logging.getLogger("sentinel-rca.nodes.execute")


async def execute_node(state: RCAState) -> dict:
    """Execute the approved action or record terminal status."""
    status = state.get("status", "")
    proposal = state.get("proposal", {})
    action = proposal.get("action", "")
    steps = state.get("step_count", 0) + 1

    if status == WorkflowStatus.ESCALATED:
        return {
            "status": WorkflowStatus.ESCALATED,
            "result": {
                "action": "escalate",
                "reason": state.get("auth_reason", "Circuit breaker or prompt injection"),
            },
            "step_count": steps,
        }

    if status == WorkflowStatus.REJECTED:
        reviewer = (state.get("hitl_response") or {}).get("reviewer_role", "unknown")
        return {
            "status": WorkflowStatus.REJECTED,
            "result": {"action": action, "decision": "rejected", "reviewer": reviewer},
            "step_count": steps,
        }

    try:
        if action == "deduplicate":
            result = {
                "action": "deduplicate",
                "message": "Incident deduplicated. No external action taken.",
            }

        elif action == "create_issue":
            title = f"{state.get('exception_type', 'Issue')}: {proposal.get('root_cause', '')[:80]}"
            issue_args = {
                "title": title,
                "root_cause": proposal.get("root_cause", ""),
                "commit_sha": proposal.get("commit_sha", ""),
                "severity": proposal.get("severity", "medium"),
                "exception_type": state.get("exception_type", ""),
                "file_name": state.get("target_file", ""),
            }
            issue = await call_tool("tracker", "create_issue", issue_args, IssueRecord)
            result = {"action": "create_issue", "issue": issue.model_dump(mode="json")}

        elif action == "draft_hotfix_pr":
            pr_args = {
                "commit_sha": proposal.get("commit_sha", ""),
                "file_path": proposal.get("file_path", ""),
                "patch_summary": proposal.get("patch_summary") or "Auto-generated hotfix patch",
            }
            pr = await call_tool("tracker", "draft_hotfix_pr", pr_args, PRPreview)
            result = {"action": "draft_hotfix_pr", "pr": pr.model_dump(mode="json")}

        else:
            return {
                "status": WorkflowStatus.ESCALATED,
                "result": {"action": "escalate", "reason": f"Unknown action: {action}"},
                "step_count": steps,
            }

        return {"status": WorkflowStatus.COMPLETED, "result": result, "step_count": steps}

    except Exception as ex:
        logger.error("execute failed for action %s: %s", action, ex)
        return {
            "status": WorkflowStatus.ERROR,
            "result": {"action": action, "error": str(ex)},
            "errors": state.get("errors", []) + [str(ex)],
            "step_count": steps,
        }
