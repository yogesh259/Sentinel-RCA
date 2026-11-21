"""Pydantic v2 schemas for execution traces and observability telemetry."""

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
    token_cost: float = 0.0
    tokens_used: int = 0
    mode: str = ""  # Active model or provider (e.g. "groq/llama-3.3-70b-versatile")
    final_verdict: str = ""
    created_at: datetime | None = None
