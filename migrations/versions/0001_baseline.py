"""Baseline schema: incidents, issues, execution_traces.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE incidents (
            id               SERIAL PRIMARY KEY,
            fingerprint      TEXT NOT NULL,
            exception_type   TEXT NOT NULL,
            file_name        TEXT NOT NULL,
            line_number      INTEGER NOT NULL,
            title            TEXT NOT NULL DEFAULT '',
            redacted_payload JSONB NOT NULL DEFAULT '{}',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_incidents_fingerprint ON incidents (fingerprint);

        CREATE TABLE issues (
            id             SERIAL PRIMARY KEY,
            title          TEXT NOT NULL,
            exception_type TEXT NOT NULL,
            file_name      TEXT NOT NULL DEFAULT '',
            root_cause     TEXT NOT NULL DEFAULT '',
            commit_sha     TEXT NOT NULL DEFAULT '',
            severity       TEXT NOT NULL DEFAULT 'medium',
            status         TEXT NOT NULL DEFAULT 'open',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_issues_exception_file ON issues (exception_type, file_name);

        CREATE TABLE execution_traces (
            id               SERIAL PRIMARY KEY,
            thread_id        TEXT NOT NULL,
            incident_id      INTEGER REFERENCES incidents (id) ON DELETE SET NULL,
            fingerprint      TEXT NOT NULL,
            redacted_payload JSONB NOT NULL DEFAULT '{}',
            nodes_visited    JSONB NOT NULL DEFAULT '[]',
            mcp_tool_calls   JSONB NOT NULL DEFAULT '[]',
            latency_ms       DOUBLE PRECISION NOT NULL DEFAULT 0,
            cost_usd         DOUBLE PRECISION NOT NULL DEFAULT 0,
            tokens_used      INTEGER NOT NULL DEFAULT 0,
            models_used      TEXT NOT NULL DEFAULT '',
            final_verdict    TEXT NOT NULL DEFAULT '',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_traces_thread ON execution_traces (thread_id);
        CREATE INDEX idx_traces_created ON execution_traces (created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE execution_traces, issues, incidents;")
