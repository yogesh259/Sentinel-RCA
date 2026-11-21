"""Pydantic v2 schemas for trace_mcp server (Phase 2)."""


from pydantic import BaseModel, Field


class ParsedStackTrace(BaseModel):
    """Structured result of parsing a raw Java/Python stack trace."""

    exception_type: str = Field(..., description="e.g. NullPointerException")
    exception_message: str = Field("", description="The error message")
    target_file: str = Field("", description="Java/Python source file name")
    class_name: str = Field("", description="Fully-qualified class or module")
    method_name: str = Field("", description="Method / function where the crash occurred")
    line_number: int = Field(0, ge=0, description="Line number in the target file (0 if unknown)")
    redaction_types: list[str] = Field(
        default_factory=list,
        description="PII/secret redaction labels that were applied",
    )
