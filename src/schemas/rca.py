"""Structured LLM output contracts.

Every LLM call in the pipeline must return one of these models. Free-form text never
drives a decision: the graph only acts on validated fields.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.schemas.mcp.tracker import SeverityLevel

Confidence = Literal["low", "medium", "high"]


def _lower(value: Any) -> Any:
    """LLMs capitalise enum values inconsistently ("High"); normalise before validation."""
    return value.strip().lower() if isinstance(value, str) else value


class Evidence(BaseModel):
    """A concrete pointer that backs the verdict, so a reviewer can verify it quickly."""

    sha: str = Field(..., description="Candidate commit SHA this evidence comes from")
    file: str = Field("", description="Repository path")
    lines: str = Field("", description="Line or range, e.g. '34' or '30-36'")
    quote: str = Field("", description="Short code excerpt from the diff or file")


class RCAVerdict(BaseModel):
    """Deep-RCA judgement over the candidate commits gathered by code."""

    suspect_sha: str | None = Field(
        None, description="SHA of the candidate commit that introduced the bug, or null"
    )
    confidence: Confidence
    severity: SeverityLevel
    root_cause: str = Field(..., min_length=1)
    mechanism: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    fix_suggestion: str = ""

    _normalise = field_validator("confidence", "severity", mode="before")(_lower)


class DedupVerdict(BaseModel):
    """Triage judgement: is the new incident the same underlying bug as an open issue?"""

    duplicate_of: int | None = Field(None, description="Matching issue id, or null")
    reason: str = ""


class TraceLocation(BaseModel):
    """LLM fallback extraction for stack-trace formats the regex parser doesn't know."""

    exception_type: str = "UnknownException"
    exception_message: str = ""
    file: str = ""
    line: int = Field(0, ge=0)
    function: str = ""
