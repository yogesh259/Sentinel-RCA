from typing import Literal

from pydantic import BaseModel, Field


class SuspectLocation(BaseModel):
    file: str
    start_line: int
    end_line: int
    reason: str
    confidence: float = Field(ge=0, le=1)


class RCAReport(BaseModel):
    title: str
    root_cause: str
    suspects: list[SuspectLocation] = Field(max_length=3)
    introduced_by: str | None
    suggested_patch: str | None
    confidence: float = Field(ge=0, le=1)


class ReviewResult(BaseModel):
    grounded: bool
    problems: list[str]


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None
