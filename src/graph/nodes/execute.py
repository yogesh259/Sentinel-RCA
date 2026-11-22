"""Execute approved actions against tracker_mcp or finalize terminal state."""

import logging

from src.graph.state import RCAState, WorkflowStatus
from src.mcp_servers import tracker_mcp

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
            issue = await tracker_mcp.create_issue(
                title=title,
                root_cause=proposal.get("root_cause", ""),
                commit_sha=proposal.get("commit_sha", ""),
                severity=proposal.get("severity", "medium"),
                exception_type=state.get("exception_type", ""),
                file_name=state.get("target_file", ""),
            )
            result = {"action": "create_issue", "issue": issue.model_dump()}

        elif action == "draft_hotfix_pr":
            pr = await tracker_mcp.draft_hotfix_pr(
                commit_sha=proposal.get("commit_sha", ""),
                file_path=proposal.get("file_path", ""),
                patch_summary=proposal.get("patch_summary", "Auto-generated hotfix patch"),
            )
            result = {"action": "draft_hotfix_pr", "pr": pr.model_dump()}

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
