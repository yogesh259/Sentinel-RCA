"""Pending HITL approvals, read straight from LangGraph checkpoints.

There is no separate approvals table or in-memory registry: a thread is "awaiting
approval" exactly when its checkpoint holds an unresolved `approval_required`
interrupt. One source of truth, so the list can never drift from what can resume.
"""

from typing import Any

APPROVAL_INTERRUPT = "approval_required"


def thread_config(thread_id: str) -> dict:
    """LangGraph run config addressing one incident's thread."""
    return {"configurable": {"thread_id": thread_id}}


async def get_pending_approval(graph: Any, thread_id: str) -> dict | None:
    """Return the approval request a paused thread is waiting on, or None."""
    snapshot = await graph.aget_state(thread_config(thread_id))
    for pending in getattr(snapshot, "interrupts", ()) or ():
        value = pending.value
        if isinstance(value, dict) and value.get("type") == APPROVAL_INTERRUPT:
            return value
    return None


async def list_pending_approvals(graph: Any, thread_ids: list[str]) -> list[dict]:
    """Return approval requests for those candidate threads that are still paused."""
    pending = []
    for thread_id in dict.fromkeys(thread_ids):
        request = await get_pending_approval(graph, thread_id)
        if request:
            pending.append(request)
    return pending


__all__ = [
    "APPROVAL_INTERRUPT",
    "get_pending_approval",
    "list_pending_approvals",
    "thread_config",
]
