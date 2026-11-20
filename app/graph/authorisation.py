"""Deterministic authorisation gate — pure Python, NO LLM calls.

Decides whether a ProposedResolution can auto-execute, needs HITL approval,
or must be blocked/escalated. Also enforces RBAC on approval decisions.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from app.core.config import settings

# ── ProposedResolution schema ─────────────────────────────────────

Action = Literal["deduplicate", "create_issue", "draft_hotfix_pr", "escalate"]
ReviewerRole = Literal["staff_sre", "junior_dev"]


class ProposedResolution(BaseModel):
    """Strict Pydantic v2 schema for a proposed action."""

    action: Action
    commit_sha: str = ""
    file_path: str = ""
    root_cause: str = ""
    severity: str = "medium"
    patch_summary: str | None = None


class AuthDecision(StrEnum):
    """Outcome of the authorisation gate."""

    AUTO_APPROVE = "auto_approve"
    HITL_REQUIRED = "hitl_required"
    BLOCKED = "blocked"


class AuthResult(BaseModel):
    """Full result of the authorisation check."""

    decision: AuthDecision
    reason: str
    proposal: ProposedResolution


# ── Prompt-injection detection ────────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"IGNORE\s+(ALL\s+)?PREVIOUS\s+INSTRUCTIONS", re.IGNORECASE),
    re.compile(r"DISREGARD\s+(ALL\s+)?PRIOR", re.IGNORECASE),
    re.compile(r"SYSTEM\s*:\s*you\s+are", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"\[\[INST\]\]", re.IGNORECASE),
    re.compile(r"DO\s+NOT\s+FOLLOW\s+(ANY\s+)?PREVIOUS", re.IGNORECASE),
    re.compile(r"OVERRIDE\s+(ALL\s+)?SAFETY", re.IGNORECASE),
]


def detect_prompt_injection(text: str) -> bool:
    """Return True if the text contains known prompt-injection patterns."""
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# ── Authorisation gate ────────────────────────────────────────────


def check_authorisation(
    proposal: ProposedResolution,
    step_count: int = 0,
    raw_trace: str = "",
) -> AuthResult:
    """Evaluate a ProposedResolution against deterministic policy rules.

    Rules:
      1. Block and escalate if step_count >= max_graph_steps (circuit breaker).
      2. Block and escalate if prompt-injection patterns are detected in raw_trace.
      3. Always require HITL if action == "draft_hotfix_pr" or severity == "CRITICAL".
      4. Auto-approve deduplicate and create_issue.
      5. Block and escalate otherwise.
    """
    max_steps = settings.max_graph_steps

    # Rule 1: Circuit breaker
    if step_count >= max_steps:
        return AuthResult(
            decision=AuthDecision.BLOCKED,
            reason=(
                f"Circuit breaker triggered: step_count ({step_count}) >= {max_steps}. Escalating."
            ),
            proposal=proposal.model_copy(update={"action": "escalate"}),
        )

    # Rule 2: Prompt injection
    if raw_trace and detect_prompt_injection(raw_trace):
        return AuthResult(
            decision=AuthDecision.BLOCKED,
            reason="Prompt-injection pattern detected in stack trace. Escalating.",
            proposal=proposal.model_copy(update={"action": "escalate"}),
        )

    # Rule 3: HITL required for draft_hotfix_pr or CRITICAL severity
    if proposal.action == "draft_hotfix_pr":
        return AuthResult(
            decision=AuthDecision.HITL_REQUIRED,
            reason="draft_hotfix_pr requires Human-in-the-Loop approval.",
            proposal=proposal,
        )

    if proposal.severity.upper() == "CRITICAL":
        return AuthResult(
            decision=AuthDecision.HITL_REQUIRED,
            reason="CRITICAL severity requires Human-in-the-Loop approval.",
            proposal=proposal,
        )

    # Rule 4: Auto-approve safe actions
    if proposal.action in ("deduplicate", "create_issue"):
        return AuthResult(
            decision=AuthDecision.AUTO_APPROVE,
            reason=f"Action '{proposal.action}' is auto-approved.",
            proposal=proposal,
        )

    # Rule 5: Fallback / unknown action
    return AuthResult(
        decision=AuthDecision.BLOCKED,
        reason=f"Action '{proposal.action}' is blocked. Escalating.",
        proposal=proposal.model_copy(update={"action": "escalate"}),
    )


# ── RBAC enforcement on approval decisions ────────────────────────


def check_rbac(
    action: Action,
    reviewer_role: ReviewerRole,
) -> tuple[bool, str]:
    """Check whether *reviewer_role* is allowed to approve *action*.

    Returns (allowed, reason).
    """
    if action == "draft_hotfix_pr":
        if reviewer_role != "staff_sre":
            return False, (
                f"Role '{reviewer_role}' is not authorized to approve '{action}'. "
                f"Requires 'staff_sre'."
            )
        return True, "Authorized: staff_sre can approve draft_hotfix_pr."

    if action == "escalate":
        return False, "Escalated incidents cannot be approved via automated gate."

    # Other actions can be approved by staff_sre or junior_dev
    return True, f"Role '{reviewer_role}' is authorized."
