"""Pydantic v2 schema for one execution_traces audit row."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionTrace(BaseModel):
    """Pydantic model representing an execution run audit trace."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    thread_id: str
    incident_id: int | None = None
    fingerprint: str = ""
    redacted_payload: dict[str, Any] = Field(default_factory=dict)
    nodes_visited: list[str] = Field(default_factory=list)
    mcp_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    tokens_used: int = 0
    models_used: str = ""
    final_verdict: str = ""
    created_at: datetime | None = None
