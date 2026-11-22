"""In-memory pending approvals registry for human-in-the-loop (HITL) reviews."""

_PENDING_APPROVALS: dict[str, dict] = {}


def register_pending_approval(
    thread_id: str,
    incident_id: int | None,
    proposal: dict,
    reason: str,
) -> None:
    """Register a pending approval requiring human-in-the-loop review."""
    _PENDING_APPROVALS[thread_id] = {
        "thread_id": thread_id,
        "incident_id": incident_id,
        "proposal": proposal,
        "reason": reason,
        "status": "awaiting_approval",
    }


def get_all_pending_approvals() -> list[dict]:
    """Retrieve all pending approvals currently awaiting review."""
    return list(_PENDING_APPROVALS.values())


def get_pending_approval(thread_id: str) -> dict | None:
    """Retrieve a specific pending approval by workflow thread_id."""
    return _PENDING_APPROVALS.get(thread_id)


def remove_pending_approval(thread_id: str) -> dict | None:
    """Remove and return a pending approval by thread_id once reviewed."""
    return _PENDING_APPROVALS.pop(thread_id, None)


def clear_pending_approvals() -> None:
    """Clear all pending approvals (used during testing or state resets)."""
    _PENDING_APPROVALS.clear()


__all__ = [
    "clear_pending_approvals",
    "get_all_pending_approvals",
    "get_pending_approval",
    "register_pending_approval",
    "remove_pending_approval",
]
