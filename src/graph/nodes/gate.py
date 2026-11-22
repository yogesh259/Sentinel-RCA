"""Authorisation gate node: deterministic action policy + HITL interrupt().

The LLM proposes a root cause; this node (pure Python, no LLM) decides what may be done
about it. Pausing via interrupt() persists the thread in the checkpointer, which is the
single source of truth for pending approvals (see src/graph/approvals.py).
"""

import logging

from langgraph.types import interrupt

from src.graph.approvals import APPROVAL_INTERRUPT
from src.graph.authorisation import (
    Action,
    AuthDecision,
    ProposedResolution,
    check_authorisation,
)
from src.graph.state import RCAState, WorkflowStatus

logger = logging.getLogger("sentinel-rca.nodes.gate")


def propose_action(state: RCAState) -> Action:
    """Choose the action from validated state fields, never from free-form LLM text."""
    forced = state.get("force_action")
    if forced:
        return forced
    if state.get("is_duplicate"):
        return "deduplicate"
    if state.get("investigation_error"):
        return "escalate"

    confident_suspect = state.get("suspect_sha") and state.get("confidence") == "high"
    serious = str(state.get("suggested_severity", "")).lower() in ("high", "critical")
    if confident_suspect and serious and state.get("patch_summary"):
        return "draft_hotfix_pr"
    return "create_issue"


def build_proposal(state: RCAState) -> ProposedResolution:
    """Assemble the proposal the policy (and, if needed, a human) will judge."""
    return ProposedResolution(
        action=propose_action(state),
        commit_sha=state.get("suspect_sha", ""),
        file_path=state.get("target_file", ""),
        root_cause=state.get("root_cause", ""),
        severity=state.get("suggested_severity", "medium"),
        confidence=state.get("confidence", "low"),
        patch_summary=state.get("patch_summary") or None,
    )


async def authorisation_gate_node(state: RCAState) -> dict:
    """Node 4: auto-approve, block, or pause for a human decision."""
    proposal = build_proposal(state)
    auth = check_authorisation(
        proposal=proposal,
        step_count=state.get("step_count", 0),
        raw_trace=state.get("raw_trace", ""),
    )
    update: dict = {
        "proposal": auth.proposal.model_dump(),
        "auth_decision": auth.decision.value,
        "auth_reason": auth.reason,
        "hitl_required": auth.decision == AuthDecision.HITL_REQUIRED,
        "step_count": state.get("step_count", 0) + 1,
    }

    if auth.decision == AuthDecision.BLOCKED:
        return {**update, "status": WorkflowStatus.ESCALATED}

    if auth.decision == AuthDecision.AUTO_APPROVE:
        return {**update, "status": WorkflowStatus.APPROVED}

    # Pauses here; the checkpoint now lists this thread as awaiting approval. On resume
    # LangGraph re-runs this node and interrupt() returns the reviewer's decision.
    hitl_response = interrupt(
        {
            "type": APPROVAL_INTERRUPT,
            "thread_id": state.get("thread_id", ""),
            "incident_id": state.get("incident_id"),
            "proposal": auth.proposal.model_dump(),
            "reason": auth.reason,
            "evidence": state.get("evidence", []),
        }
    )
    rejected = isinstance(hitl_response, dict) and hitl_response.get("decision") == "reject"
    return {
        **update,
        "hitl_response": hitl_response,
        "status": WorkflowStatus.REJECTED if rejected else WorkflowStatus.APPROVED,
    }


__all__ = ["authorisation_gate_node", "build_proposal", "propose_action"]
