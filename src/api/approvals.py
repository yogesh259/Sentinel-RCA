"""Human-in-the-loop approvals: list paused runs and apply a reviewer's decision.

The LangGraph checkpoint is the single source of truth for what is pending; the
execution_traces table only narrows which threads to look at.
"""

import logging

from fastapi import APIRouter, HTTPException

from src.graph import get_graph, get_pending_approval, list_pending_approvals
from src.graph.authorisation import check_rbac
from src.schemas.approval import ApprovalDecisionRequest, ApprovalDecisionResponse, ApprovalItem
from src.services.execution_traces import get_latest_execution_traces
from src.services.workflow_runner import resume_workflow

logger = logging.getLogger("sentinel-rca.api.approvals")

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalItem])
async def list_approvals() -> list[ApprovalItem]:
    """List incidents whose checkpoint is paused on an approval interrupt."""
    candidates = [
        t.thread_id
        for t in await get_latest_execution_traces(limit=100)
        if t.final_verdict == "AWAITING_APPROVAL"
    ]
    return [
        ApprovalItem(
            thread_id=item["thread_id"],
            incident_id=item.get("incident_id"),
            proposal=item.get("proposal", {}),
            reason=item.get("reason", ""),
            evidence=item.get("evidence", []),
        )
        for item in await list_pending_approvals(get_graph(), candidates)
    ]


@router.post("/{thread_id}/decision", response_model=ApprovalDecisionResponse)
async def submit_approval_decision(
    thread_id: str,
    body: ApprovalDecisionRequest,
) -> ApprovalDecisionResponse:
    """Enforce RBAC, then resume the paused checkpoint with the decision.

    Only 'staff_sre' may approve 'draft_hotfix_pr' (others get 403); anyone may reject.
    """
    pending = await get_pending_approval(get_graph(), thread_id)
    if not pending:
        raise HTTPException(
            status_code=404,
            detail=f"Approval thread '{thread_id}' not found or already resolved.",
        )

    action = pending.get("proposal", {}).get("action", "")
    allowed, rbac_reason = check_rbac(action, body.reviewer_role)
    if not allowed and body.decision == "approve":
        raise HTTPException(status_code=403, detail=rbac_reason)

    try:
        resumed_state = await resume_workflow(thread_id, body.decision, body.reviewer_role)
    except Exception as exc:
        logger.error("Failed to resume thread %s: %s", thread_id, exc)
        raise HTTPException(status_code=500, detail=f"Error resuming workflow thread: {exc}") from exc

    return ApprovalDecisionResponse(
        status="resumed",
        decision=body.decision,
        reviewer_role=body.reviewer_role,
        thread_id=thread_id,
        workflow_status=resumed_state.get("status"),
        result=resumed_state.get("result"),
    )
