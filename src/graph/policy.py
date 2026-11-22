"""Deterministic action policy: what to do about an incident and who may approve it.

Pure Python, no LLM. The model only judges evidence; this module turns its validated
fields into an action and decides whether that action may run unattended.
"""

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from src.graph.state import RCAState
from src.schemas.mcp.tracker import SeverityLevel
from src.schemas.rca import Confidence

Action = Literal["deduplicate", "create_issue", "draft_hotfix_pr", "escalate"]
ReviewerRole = Literal["staff_sre", "junior_dev"]


class ProposedResolution(BaseModel):
    """The action the policy proposes, with the facts a reviewer needs to judge it."""

    action: Action
    commit_sha: str = ""
    file_path: str = ""
    root_cause: str = ""
    severity: SeverityLevel = "medium"
    # A proposal that doesn't state its confidence is reviewed, not trusted.
    confidence: Confidence = "low"
    patch_summary: str | None = None


class AuthDecision(StrEnum):
    """Outcome of the authorisation gate."""

    AUTO_APPROVE = "auto_approve"
    HITL_REQUIRED = "hitl_required"
    BLOCKED = "blocked"


class AuthResult(BaseModel):
    """The gate's decision, why, and the (possibly downgraded) proposal."""

    decision: AuthDecision
    reason: str
    proposal: ProposedResolution


INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"IGNORE\s+(ALL\s+)?PREVIOUS\s+INSTRUCTIONS",
        r"DISREGARD\s+(ALL\s+)?PRIOR",
        r"SYSTEM\s*:\s*you\s+are",
        r"<\|im_start\|>",
        r"\[\[INST\]\]",
        r"DO\s+NOT\s+FOLLOW\s+(ANY\s+)?PREVIOUS",
        r"OVERRIDE\s+(ALL\s+)?SAFETY",
    )
]


def detect_prompt_injection(text: str) -> bool:
    """Return True if the text contains a known prompt-injection pattern."""
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def propose_action(state: RCAState) -> Action:
    """Choose the action from validated state fields, never from free-form LLM text."""
    if state.get("is_duplicate"):
        return "deduplicate"
    if state.get("investigation_error"):
        return "escalate"

    confident_suspect = state.get("suspect_sha") and state.get("confidence") == "high"
    serious = state.get("suggested_severity") in ("high", "critical")
    if confident_suspect and serious and state.get("patch_summary"):
        return "draft_hotfix_pr"
    return "create_issue"


def build_proposal(state: RCAState) -> ProposedResolution:
    """Assemble the proposal the gate (and, if needed, a human) will judge."""
    return ProposedResolution(
        action=propose_action(state),
        commit_sha=state.get("suspect_sha", ""),
        file_path=state.get("target_file", ""),
        root_cause=state.get("root_cause", ""),
        severity=state.get("suggested_severity", "medium"),
        confidence=state.get("confidence", "low"),
        patch_summary=state.get("patch_summary") or None,
    )


def check_authorisation(proposal: ProposedResolution, raw_trace: str = "") -> AuthResult:
    """Decide whether a proposal runs automatically, waits for a human, or is escalated.

    Order matters: escalation beats review, and review beats auto-approval.
    """
    if detect_prompt_injection(raw_trace):
        return escalate(proposal, "Prompt-injection pattern detected in stack trace.")
    if proposal.action == "escalate":
        return escalate(proposal, "Investigation failed; a human must take over.")

    if proposal.action == "draft_hotfix_pr":
        return review(proposal, "draft_hotfix_pr requires Human-in-the-Loop approval.")
    if proposal.severity == "critical":
        return review(proposal, "CRITICAL severity requires Human-in-the-Loop approval.")
    if proposal.action == "create_issue" and proposal.confidence == "low":
        return review(proposal, "Low-confidence root cause requires Human-in-the-Loop confirmation.")

    return AuthResult(
        decision=AuthDecision.AUTO_APPROVE,
        reason=f"Action '{proposal.action}' is auto-approved.",
        proposal=proposal,
    )


def escalate(proposal: ProposedResolution, reason: str) -> AuthResult:
    """Block the proposal and hand the incident to a human as an escalation."""
    return AuthResult(
        decision=AuthDecision.BLOCKED,
        reason=f"{reason} Escalating.",
        proposal=proposal.model_copy(update={"action": "escalate"}),
    )


def review(proposal: ProposedResolution, reason: str) -> AuthResult:
    """Pause the proposal until a reviewer approves or rejects it."""
    return AuthResult(decision=AuthDecision.HITL_REQUIRED, reason=reason, proposal=proposal)


def check_rbac(action: Action, reviewer_role: ReviewerRole) -> tuple[bool, str]:
    """Return whether *reviewer_role* may approve *action*, and why."""
    if action == "draft_hotfix_pr" and reviewer_role != "staff_sre":
        return False, f"Role '{reviewer_role}' is not authorized to approve '{action}'. Requires 'staff_sre'."
    return True, f"Role '{reviewer_role}' is authorized."
