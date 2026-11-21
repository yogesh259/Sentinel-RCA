"""Pydantic v2 schemas for inbound Sentry webhook payloads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Inbound Sentry webhook payload ─────────────────────────────────


class SentryStacktraceFrame(BaseModel):
    """A single frame from the Sentry stacktrace."""

    filename: str = ""
    lineno: int = 0
    function: str = ""
    context_line: str | None = None


class SentryStacktrace(BaseModel):
    frames: list[SentryStacktraceFrame] = Field(default_factory=list)


class SentryExceptionValue(BaseModel):
    """Normalized Sentry exception item within event.exception.values."""

    type: str = Field(..., description="Exception class, e.g. NullPointerException")
    value: str = Field("", description="Exception message")
    module: str | None = Field(None, description="Module / package")
    stacktrace: SentryStacktrace | None = None


# Backward-compatible aliases for legacy imports
SentryException = SentryExceptionValue
SentryExceptionEntry = SentryExceptionValue


class SentryExceptionPayload(BaseModel):
    """The 'exception' container inside Sentry event data."""

    values: list[SentryExceptionValue] = Field(default_factory=list)


class SentryEventData(BaseModel):
    """The 'data' sub-object inside a Sentry webhook delivery."""

    event_id: str = ""
    title: str = ""
    culprit: str | None = None
    timestamp: float | None = None
    exception: SentryExceptionPayload | dict[str, Any] | None = None


class SentryWebhookPayload(BaseModel):
    """Top-level Sentry webhook payload."""

    action: str = "created"
    data: dict[str, Any] = Field(default_factory=dict)
    actor: dict[str, Any] | None = None
