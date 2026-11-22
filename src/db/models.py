"""ORM entities for the tables in the database"""

from datetime import datetime
from typing import Annotated, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Reusable column shapes.
IntPk = Annotated[int, mapped_column(primary_key=True)]
CreatedAt = Annotated[datetime, mapped_column(server_default=func.now())]
JsonObject = Annotated[dict[str, Any], mapped_column(server_default=text("'{}'"))]
JsonList = Annotated[list, mapped_column(server_default=text("'[]'"))]


class Base(DeclarativeBase):
    # Python type to Postgres column type, so most columns need no explicit type.
    type_annotation_map = {
        str: Text(),
        int: Integer(),
        float: DOUBLE_PRECISION(),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB(),
        list: JSONB(),
    }
    __mapper_args__ = {"eager_defaults": True}


class IncidentRow(Base):
    """An error event"""

    __tablename__ = "incidents"
    __table_args__ = (Index("idx_incidents_fingerprint", "fingerprint"),)

    id: Mapped[IntPk]
    fingerprint: Mapped[str]
    exception_type: Mapped[str]
    file_name: Mapped[str]
    line_number: Mapped[int]
    title: Mapped[str] = mapped_column(server_default="")
    redacted_payload: Mapped[JsonObject]
    created_at: Mapped[CreatedAt]


class IssueRow(Base):
    """An issue corresponding to an error event"""

    __tablename__ = "issues"
    __table_args__ = (Index("idx_issues_exception_file", "exception_type", "file_name"),)

    id: Mapped[IntPk]
    title: Mapped[str]
    exception_type: Mapped[str]
    file_name: Mapped[str] = mapped_column(server_default="")
    root_cause: Mapped[str] = mapped_column(server_default="")
    commit_sha: Mapped[str] = mapped_column(server_default="")
    severity: Mapped[str] = mapped_column(server_default="medium")  # low | medium | high | critical
    status: Mapped[str] = mapped_column(server_default="open")  # open | in_progress | resolved | closed
    created_at: Mapped[CreatedAt]


class ExecutionTraceRow(Base):
    """Audit record of one RCA run"""

    __tablename__ = "execution_traces"
    __table_args__ = (
        Index("idx_traces_thread", "thread_id"),
        Index("idx_traces_created", text("created_at DESC")),
    )

    id: Mapped[IntPk]
    thread_id: Mapped[str]
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))
    fingerprint: Mapped[str]
    redacted_payload: Mapped[JsonObject]
    nodes_visited: Mapped[JsonList]
    mcp_tool_calls: Mapped[JsonList]
    latency_ms: Mapped[float] = mapped_column(server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(server_default=text("0"))
    tokens_used: Mapped[int] = mapped_column(server_default=text("0"))
    models_used: Mapped[str] = mapped_column(server_default="")  # models used, e.g. "groq/llama-3.3-70b-versatile"
    final_verdict: Mapped[str] = mapped_column(server_default="")
    created_at: Mapped[CreatedAt]
