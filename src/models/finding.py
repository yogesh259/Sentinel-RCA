from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    file: str
    start_line: int
    end_line: int
    snippet: str
    why_relevant: str


class Finding(BaseModel):
    source: Literal["locator", "history"]
    summary: str
    evidence: list[Evidence]
    confidence: float = Field(ge=0, le=1)
