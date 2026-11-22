"""Authorisation gate node: applies the action policy and pauses for a human via interrupt().

Pausing persists the thread in the checkpointer, which is the single source of truth
for pending approvals (see src/graph/approvals.py).
"""

from langgraph.types import interrupt

from src.graph.approvals import APPROVAL_INTERRUPT
from src.graph.policy import AuthDecision, build_proposal, check_authorisation
from src.graph.state import RCAState, WorkflowStatus


async def authorisation_gate_node(state: RCAState) -> dict:
    """Node 4: auto-approve, escalate, or pause for a human decision."""
    auth = check_authorisation(build_proposal(state), raw_trace=state.get("raw_trace", ""))
    update = {
        "proposal": auth.proposal.model_dump(),
        "auth_decision": auth.decision.value,
        "auth_reason": auth.reason,
    }

    if auth.decision == AuthDecision.BLOCKED:
        return {**update, "status": WorkflowStatus.ESCALATED}
    if auth.decision == AuthDecision.AUTO_APPROVE:
        return {**update, "status": WorkflowStatus.APPROVED}

    # The run stops here until a reviewer decides. On resume LangGraph re-runs this node
    # from the top, and interrupt() returns the decision instead of pausing again.
    hitl_response = interrupt(
        {
            "type": APPROVAL_INTERRUPT,
            "thread_id": state.get("thread_id", ""),
            "incident_id": state.get("incident_id"),
            "proposal": update["proposal"],
            "reason": auth.reason,
            "evidence": state.get("evidence", []),
        }
    )
    rejected = hitl_response.get("decision") == "reject"
    return {
        **update,
        "hitl_response": hitl_response,
        "status": WorkflowStatus.REJECTED if rejected else WorkflowStatus.APPROVED,
    }
