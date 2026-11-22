"""Read-only audit log of RCA runs, consumed by the Streamlit Ops Console."""

from fastapi import APIRouter

from src.schemas.trace import ExecutionTrace
from src.services.execution_traces import get_latest_execution_traces

router = APIRouter(prefix="/v1/traces", tags=["traces"])


@router.get("", response_model=list[ExecutionTrace])
async def list_execution_traces(limit: int = 20) -> list[ExecutionTrace]:
    """Return the most recent execution traces, newest first."""
    return await get_latest_execution_traces(limit=limit)
