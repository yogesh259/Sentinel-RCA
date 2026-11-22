"""The shared state every RCA graph node reads from and writes to."""

import operator
from enum import StrEnum
from typing import Annotated, Any, TypedDict


class WorkflowStatus(StrEnum):
    """Where a run ended up after the authorisation gate."""

    APPROVED = "approved"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    ERROR = "error"


class RCAState(TypedDict, total=False):
    """One incident's run. Nodes return only the keys they change; LangGraph merges them."""

    # Ingest
    thread_id: str
    incident_id: int | None
    fingerprint: str
    raw_payload: dict[str, Any]
    raw_trace: str

    # parse_trace
    exception_type: str
    exception_message: str
    target_file: str
    line_number: int
    frames: list[dict[str, Any]]

    # dedup
    is_duplicate: bool
    dedup_summary: str

    # investigate_git
    candidates: list[dict[str, Any]]
    suspect_sha: str
    confidence: str
    root_cause: str
    mechanism: str
    evidence: list[dict[str, Any]]
    suggested_severity: str
    patch_summary: str
    investigation_error: str
    guardrail_events: list[str]

    # authorisation_gate
    proposal: dict[str, Any]
    auth_decision: str
    auth_reason: str
    hitl_response: dict[str, Any] | None

    # execute_action
    status: WorkflowStatus
    result: dict[str, Any] | None

    # Audit trail: appended to by every node instead of overwritten.
    nodes_visited: Annotated[list[str], operator.add]
    llm_calls: Annotated[list[dict[str, Any]], operator.add]
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
