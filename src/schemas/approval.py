"""Pydantic v2 schemas for Human-in-the-Loop approvals and decisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ── Human-in-the-Loop Approvals ───────────────────────────────────

ReviewerRole = Literal["staff_sre", "junior_dev"]
DecisionType = Literal["approve", "reject"]


class ApprovalItem(BaseModel):
    """A paused RCA thread awaiting human decision."""

    thread_id: str
    incident_id: int | None = None
    proposal: dict[str, Any]
    reason: str
    status: str = "awaiting_approval"


class ApprovalDecisionRequest(BaseModel):
    """Request payload for POST /v1/approvals/{thread_id}/decision."""

    decision: DecisionType
    reviewer_role: ReviewerRole


class ApprovalDecisionResponse(BaseModel):
    """Response payload for POST /v1/approvals/{thread_id}/decision."""

    status: str
    decision: DecisionType
    reviewer_role: ReviewerRole
    thread_id: str
    workflow_status: str | None = None
    result: dict[str, Any] | None = None
