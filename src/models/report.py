from typing import Literal

from pydantic import BaseModel, Field


class SuspectLocation(BaseModel):
    file: str
    start_line: int
    end_line: int
    reason: str
    confidence: float = Field(ge=0, le=1)


class ReportBase(BaseModel):
    title: str
    root_cause: str
    suspects: list[SuspectLocation] = Field(max_length=3)
    introduced_by: str | None
    confidence: float = Field(ge=0, le=1)


class CodeEdit(BaseModel):
    file: str
    find: str
    replace: str


class DraftReport(ReportBase):
    """What the RCA writer model returns. The diff is built from `fix` in code, not written by the model."""

    fix: list[CodeEdit] = Field(default_factory=list, max_length=3)


class RCAReport(ReportBase):
    suggested_patch: str | None


class ReviewResult(BaseModel):
    grounded: bool
    problems: list[str]


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None
