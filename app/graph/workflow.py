"""LangGraph coordinator — stateful RCA workflow with checkpointer and HITL interrupt.

Graph pipeline:
  parse_trace (trace_mcp)
    → semantic_dedup (Triage LLM + tracker_mcp)
    → investigate_git (Deep LLM + git_mcp)
    → authorisation_gate (Deterministic policy engine + interrupt)
    → execute_action (tracker_mcp)

Uses LangGraph interrupt() at authorisation_gate whenever HITL is required,
persisting the paused state to the configured checkpointer.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.graph.authorisation import (
    Action,
    AuthDecision,
    ProposedResolution,
    check_authorisation,
)
from app.schemas.mcp.git import CommitInfo

logger = logging.getLogger("sentinel-rca.workflow")


# ── In-Memory Pending Approvals Registry ──────────────────────────

_PENDING_APPROVALS: dict[str, dict] = {}


def register_pending_approval(
    thread_id: str,
    incident_id: int | None,
    proposal: dict,
    reason: str,
) -> None:
    _PENDING_APPROVALS[thread_id] = {
        "thread_id": thread_id,
        "incident_id": incident_id,
        "proposal": proposal,
        "reason": reason,
        "status": "awaiting_approval",
    }


def get_all_pending_approvals() -> list[dict]:
    return list(_PENDING_APPROVALS.values())


def get_pending_approval(thread_id: str) -> dict | None:
    return _PENDING_APPROVALS.get(thread_id)


def remove_pending_approval(thread_id: str) -> dict | None:
    return _PENDING_APPROVALS.pop(thread_id, None)


def clear_pending_approvals() -> None:
    _PENDING_APPROVALS.clear()


# ── Shared LangGraph State ─────────────────────────────────────────


class RCAState(TypedDict, total=False):
    # Inputs
    thread_id: str
    incident_id: int | None
    fingerprint: str
    raw_trace: str
    raw_payload: dict

    # After parse_trace
    exception_type: str
    exception_message: str
    target_file: str
    class_name: str
    method_name: str
    line_number: int
    redaction_types: list[str]

    # After semantic_dedup
    is_duplicate: bool
    existing_issues: list[dict]
    triage_summary: str

    # After investigate_git
    suspect_commits: list[dict]
    root_cause: str
    suggested_severity: str
    patch_summary: str

    # Proposed resolution & authorisation
    force_action: str | None
    proposal: dict
    auth_decision: str
    auth_reason: str
    hitl_required: bool
    hitl_response: dict | None

    # Execution result
    status: str
    result: dict | None
    step_count: int
    cost: dict
    errors: list[str]


# ── Graph Nodes ────────────────────────────────────────────────────


async def parse_trace_node(state: RCAState) -> dict:
    """Node 1: Parse raw stack trace via trace_mcp (runs redaction first)."""
    from app.core.redaction import redact_dict
    from app.mcp_servers.git_mcp import resolve_victim_file
    from app.mcp_servers.trace_mcp import parse_stack_trace

    payload = state.get("raw_payload", {})
    dict_redactions: list[str] = []
    if payload:
        payload, dict_redactions = redact_dict(payload)

    raw = state.get("raw_trace", "")
    if not raw:
        event = payload.get("data", {}).get("event", payload.get("data", {}))
        exc_info = event.get("exception", {})
        values = exc_info.get("values", [])
        if values:
            top = values[0]
            exc_type = top.get("type", "UnknownException")
            exc_val = top.get("value", "")
            frames = top.get("stacktrace", {}).get("frames", [])
            lines = [f"{exc_type}: {exc_val}"]
            culprit = event.get("culprit", "")
            for f in frames:
                fn = f.get("filename", "?")
                ln = f.get("lineno", 0)
                func = f.get("function", "?")
                if culprit and "." not in func and culprit.endswith(func):
                    func = culprit
                lines.append(f"\tat {func}({fn}:{ln})")
            raw = "\n".join(lines)

    parsed = parse_stack_trace(raw)
    target_file = parsed.target_file
    line_number = parsed.line_number
    class_name = parsed.class_name
    method_name = parsed.method_name

    # Fallback to checking payload frames directly if parsing left target_file empty
    if not target_file or target_file in ("?", "unknown"):
        event = payload.get("data", {}).get("event", payload.get("data", {}))
        exc_info = event.get("exception", {})
        values = exc_info.get("values", [])
        if values:
            frames = values[0].get("stacktrace", {}).get("frames", [])
            for f in reversed(frames):
                fn = f.get("filename")
                if fn and fn not in ("?", "unknown"):
                    target_file = fn
                    if not line_number and f.get("lineno"):
                        try:
                            line_number = int(f["lineno"])
                        except (ValueError, TypeError):
                            pass
                    if not method_name and f.get("function"):
                        method_name = str(f["function"])
                    if not class_name and fn.endswith(".java"):
                        class_name = fn.removesuffix(".java")
                    break

    resolved_file = resolve_victim_file(target_file) if target_file else ""
    final_target = resolved_file or target_file
    current_steps = state.get("step_count", 0)

    combined_redactions = list(parsed.redaction_types)
    for dr in dict_redactions:
        if dr not in combined_redactions:
            combined_redactions.append(dr)

    return {
        "raw_payload": payload,
        "raw_trace": raw,
        "exception_type": parsed.exception_type,
        "exception_message": parsed.exception_message,
        "target_file": final_target,
        "class_name": class_name,
        "method_name": method_name,
        "line_number": line_number,
        "redaction_types": combined_redactions,
        "step_count": current_steps + 1,
    }


async def semantic_dedup_node(state: RCAState) -> dict:
    """Node 2: Search existing issues via tracker_mcp; triage classification."""
    from app.mcp_servers import tracker_mcp

    exception_type = state.get("exception_type", "")
    target_file = state.get("target_file", "")
    current_steps = state.get("step_count", 0)

    try:
        existing = await tracker_mcp.search_existing_issues(exception_type, target_file)
        existing_dicts = [e.model_dump() for e in existing]
    except Exception as exc:
        logger.warning("tracker_mcp search failed: %s", exc)
        existing_dicts = []

    is_duplicate = len(existing_dicts) > 0
    triage_summary = (
        f"Found {len(existing_dicts)} existing issue(s) for {exception_type} in {target_file}."
    )
    if is_duplicate:
        triage_summary += " Marked as duplicate."

    return {
        "is_duplicate": is_duplicate,
        "existing_issues": existing_dicts,
        "triage_summary": triage_summary,
        "step_count": current_steps + 1,
    }


async def investigate_git_node(state: RCAState) -> dict:
    """Node 3: Investigate git history and correlate diffs."""
    current_steps = state.get("step_count", 0)

    if state.get("is_duplicate"):
        return {
            "root_cause": "Duplicate of previously tracked issue.",
            "suggested_severity": "low",
            "patch_summary": "",
            "step_count": current_steps + 1,
        }

    from app.mcp_servers.git_mcp import (
        GitInputValidationError,
        get_commit_diff,
        get_recent_commits,
        resolve_victim_file,
    )

    raw_target_file = state.get("target_file", "")
    target_file = resolve_victim_file(raw_target_file) if raw_target_file else ""
    exception_type = state.get("exception_type", "")
    exception_message = state.get("exception_message", "")

    commits: list[CommitInfo] = []
    if target_file:
        candidate_paths = [target_file]
        if "/" not in target_file:
            candidate_paths.append(f"src/main/java/com/victim/controller/{target_file}")
            candidate_paths.append(f"src/main/java/com/victim/service/{target_file}")

        for cp in candidate_paths:
            try:
                commits = get_recent_commits(cp, limit=5)
                if commits:
                    target_file = cp
                    break
            except (GitInputValidationError, RuntimeError, FileNotFoundError):
                continue

    suspect_dicts = [c.model_dump() for c in commits]
    root_cause = f"{exception_type} in {target_file}" if target_file else exception_type
    patch_summary = ""
    suggested_severity = state.get("suggested_severity") or "medium"

    if commits:
        try:
            diff = get_commit_diff(commits[0].sha)
            diff_text = diff.diff

            if "NullPointer" in exception_type or "null" in exception_message.lower():
                suggested_severity = "high"
                root_cause = (
                    f"NullPointerException in {target_file}:{state.get('line_number', 0)}. "
                    f"Likely introduced by commit {commits[0].short_sha} ('{commits[0].message}')."
                )
                patch_summary = f"Add null check for argument in {target_file}"
            elif "leak" in diff_text.lower() or "close()" in diff_text.lower():
                suggested_severity = "high"
                root_cause = (
                    f"Connection/resource leak in {target_file}. "
                    f"Introduced by commit {commits[0].short_sha}."
                )
                patch_summary = f"Ensure connection/stream is closed in {target_file}"
            else:
                root_cause = f"{exception_type} in {target_file} (commit {commits[0].short_sha})"
                patch_summary = f"Resolve {exception_type} in {target_file}"
        except Exception as exc:
            logger.warning("get_commit_diff error: %s", exc)

    return {
        "target_file": target_file,
        "suspect_commits": suspect_dicts,
        "root_cause": root_cause,
        "suggested_severity": suggested_severity,
        "patch_summary": patch_summary,
        "step_count": current_steps + 1,
    }


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
        update["status"] = "escalated"
        update["hitl_required"] = False
        return update

    if auth.decision == AuthDecision.HITL_REQUIRED:
        thread_id = state.get("thread_id", "default")
        incident_id = state.get("incident_id")
        update["hitl_required"] = True
        update["status"] = "awaiting_approval"

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
            update["status"] = "rejected"
        else:
            update["status"] = "approved"

        return update

    # AUTO_APPROVE
    update["hitl_required"] = False
    update["status"] = "approved"
    return update


async def execute_action_node(state: RCAState) -> dict:
    """Node 5: Execute action via tracker_mcp or finalize status."""
    from app.mcp_servers import tracker_mcp

    current_steps = state.get("step_count", 0)
    proposal_dict = state.get("proposal", {})
    action = proposal_dict.get("action", "")
    status = state.get("status", "")

    if status == "escalated":
        return {
            "status": "escalated",
            "result": {
                "action": "escalate",
                "reason": state.get("auth_reason", "Circuit breaker or prompt injection"),
            },
            "step_count": current_steps + 1,
        }

    if status == "rejected":
        return {
            "status": "rejected",
            "result": {
                "action": action,
                "decision": "rejected",
                "reviewer": (state.get("hitl_response") or {}).get("reviewer_role", "unknown"),
            },
            "step_count": current_steps + 1,
        }

    try:
        if action == "deduplicate":
            return {
                "status": "completed",
                "result": {
                    "action": "deduplicate",
                    "message": "Incident deduplicated. No external action taken.",
                },
                "step_count": current_steps + 1,
            }

        if action == "create_issue":
            exc_label = state.get("exception_type", "Issue")
            cause_prefix = proposal_dict.get("root_cause", "")[:80]
            issue = await tracker_mcp.create_issue(
                title=f"{exc_label}: {cause_prefix}",
                root_cause=proposal_dict.get("root_cause", ""),
                commit_sha=proposal_dict.get("commit_sha", ""),
                severity=proposal_dict.get("severity", "medium"),
                exception_type=state.get("exception_type", ""),
                file_name=state.get("target_file", ""),
            )
            return {
                "status": "completed",
                "result": {"action": "create_issue", "issue": issue.model_dump()},
                "step_count": current_steps + 1,
            }

        if action == "draft_hotfix_pr":
            pr = await tracker_mcp.draft_hotfix_pr(
                commit_sha=proposal_dict.get("commit_sha", ""),
                file_path=proposal_dict.get("file_path", ""),
                patch_summary=proposal_dict.get("patch_summary", "Auto-generated hotfix patch"),
            )
            return {
                "status": "completed",
                "result": {"action": "draft_hotfix_pr", "pr": pr.model_dump()},
                "step_count": current_steps + 1,
            }

        return {
            "status": "escalated",
            "result": {"action": "escalate", "reason": f"Unknown action: {action}"},
            "step_count": current_steps + 1,
        }

    except Exception as exc:
        logger.error("execute_action error: %s", exc)
        return {
            "status": "error",
            "result": {"action": action, "error": str(exc)},
            "errors": state.get("errors", []) + [str(exc)],
            "step_count": current_steps + 1,
        }


# ── Graph Builder & Checkpointer ───────────────────────────────────

_active_checkpointer: BaseCheckpointSaver | None = None
_compiled_graph = None


def get_default_checkpointer() -> BaseCheckpointSaver:
    global _active_checkpointer
    if _active_checkpointer is None:
        _active_checkpointer = MemorySaver()
    return _active_checkpointer


def set_default_checkpointer(checkpointer: BaseCheckpointSaver) -> None:
    global _active_checkpointer, _compiled_graph
    _active_checkpointer = checkpointer
    _compiled_graph = None


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compile and return the stateful RCA LangGraph."""
    cp = checkpointer or get_default_checkpointer()

    builder = StateGraph(RCAState)

    builder.add_node("parse_trace", parse_trace_node)
    builder.add_node("semantic_dedup", semantic_dedup_node)
    builder.add_node("investigate_git", investigate_git_node)
    builder.add_node("authorisation_gate", authorisation_gate_node)
    builder.add_node("execute_action", execute_action_node)

    builder.set_entry_point("parse_trace")
    builder.add_edge("parse_trace", "semantic_dedup")
    builder.add_edge("semantic_dedup", "investigate_git")
    builder.add_edge("investigate_git", "authorisation_gate")
    builder.add_edge("authorisation_gate", "execute_action")
    builder.add_edge("execute_action", END)

    return builder.compile(checkpointer=cp)


def get_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Return the active compiled graph singleton or build new one."""
    global _compiled_graph
    if _compiled_graph is None or checkpointer is not None:
        _compiled_graph = build_graph(checkpointer)
    return _compiled_graph


def reset_graph() -> None:
    """Reset graph and in-memory approvals for clean test isolation."""
    global _compiled_graph, _active_checkpointer
    _compiled_graph = None
    _active_checkpointer = None
    clear_pending_approvals()
