"""Authorisation gate node: policy check + LangGraph interrupt()."""

import logging

from langgraph.types import interrupt

from src.graph.approvals import register_pending_approval, remove_pending_approval
from src.graph.authorisation import (
    Action,
    AuthDecision,
    ProposedResolution,
    check_authorisation,
)
from src.graph.state import RCAState, WorkflowStatus

logger = logging.getLogger("sentinel-rca.nodes.gate")


async def authorisation_gate_node(state: RCAState) -> dict:
    """Node 4: Deterministic authorisation gate.

    Calls interrupt() if HITL is required, pausing the workflow.
    """
    current_steps = state.get("step_count", 0)
    raw_trace = state.get("raw_trace", "")
    force_action = state.get("force_action")
    is_duplicate = state.get("is_duplicate", False)
    suggested_severity = state.get("suggested_severity", "medium")
    patch_summary = state.get("patch_summary", "")

    # Determine proposed action
    if force_action in ("deduplicate", "create_issue", "draft_hotfix_pr", "escalate"):
        action: Action = force_action  # type: ignore
    elif is_duplicate:
        action = "deduplicate"
    elif patch_summary and suggested_severity.lower() in ("high", "critical"):
        action = "draft_hotfix_pr"
    else:
        action = "create_issue"

    commit_sha = ""
    suspects = state.get("suspect_commits", [])
    if suspects:
        commit_sha = suspects[0].get("sha", "")

    proposal = ProposedResolution(
        action=action,
        commit_sha=commit_sha,
        file_path=state.get("target_file", ""),
        root_cause=state.get("root_cause", ""),
        severity=suggested_severity,
        patch_summary=patch_summary or None,
    )

    auth = check_authorisation(
        proposal=proposal,
        step_count=current_steps,
        raw_trace=raw_trace,
    )

    update: dict = {
        "proposal": proposal.model_dump(),
        "auth_decision": auth.decision.value,
        "auth_reason": auth.reason,
        "step_count": current_steps + 1,
    }

    if auth.decision == AuthDecision.BLOCKED:
        update["status"] = WorkflowStatus.ESCALATED
        update["hitl_required"] = False
        return update

    if auth.decision == AuthDecision.HITL_REQUIRED:
        thread_id = state.get("thread_id", "default")
        incident_id = state.get("incident_id")
        update["hitl_required"] = True
        update["status"] = WorkflowStatus.AWAITING_APPROVAL

        register_pending_approval(
            thread_id=thread_id,
            incident_id=incident_id,
            proposal=proposal.model_dump(),
            reason=auth.reason,
        )

        # Interrupt workflow for human-in-the-loop review
        hitl_response = interrupt(
            {
                "type": "approval_required",
                "thread_id": thread_id,
                "proposal": proposal.model_dump(),
                "reason": auth.reason,
            }
        )

        # Resumed with Command(resume=...)
        remove_pending_approval(thread_id)
        update["hitl_response"] = hitl_response

        if isinstance(hitl_response, dict) and hitl_response.get("decision") == "reject":
            update["status"] = WorkflowStatus.REJECTED
        else:
            update["status"] = WorkflowStatus.APPROVED

        return update

    # AUTO_APPROVE
    update["hitl_required"] = False
    update["status"] = WorkflowStatus.APPROVED
    return update


__all__ = ["authorisation_gate_node"]
