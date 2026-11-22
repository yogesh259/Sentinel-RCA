"""Pending HITL approvals, read straight from LangGraph checkpoints.

There is no separate approvals table or in-memory registry: a thread is "awaiting
approval" exactly when its checkpoint holds an unresolved `approval_required`
interrupt. One source of truth, so the list can never drift from what can resume.
"""

from langgraph.graph.state import CompiledStateGraph

APPROVAL_INTERRUPT = "approval_required"


def thread_config(thread_id: str) -> dict:
    """LangGraph run config addressing one incident's thread."""
    return {"configurable": {"thread_id": thread_id}}


async def get_pending_approval(graph: CompiledStateGraph, thread_id: str) -> dict | None:
    """Return the approval request a paused thread is waiting on, or None."""
    snapshot = await graph.aget_state(thread_config(thread_id))
    for pending in snapshot.interrupts:
        if isinstance(pending.value, dict) and pending.value.get("type") == APPROVAL_INTERRUPT:
            return pending.value
    return None


async def list_pending_approvals(graph: CompiledStateGraph, thread_ids: list[str]) -> list[dict]:
    """Return approval requests for those candidate threads that are still paused."""
    pending = []
    for thread_id in dict.fromkeys(thread_ids):
        request = await get_pending_approval(graph, thread_id)
        if request:
            pending.append(request)
    return pending
