"""Interactions table — unified event stream (原语 ①).

The hermes-crm first-principles doc puts events as first-class citizens and
state as derived; this table is the single append-only log every subsystem
writes to when "something happened" (email, call, meeting, CRM write, agent
action, ...).

Schema follows §4.3 of ``docs/2026-04-15-hermes-crm-implementation-plan.md``
verbatim. We intentionally don't foreign-key ``subjects`` / ``actors`` because
they're polymorphic (``{"module": "accounts", "id": 17}`` today, could be
``{"type": "external_contact", "channel": "feishu", "id": "ou_xxx"}`` later)
— integrity is enforced at the application boundary, same policy as
changelog.data_id.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-16
"""
from __future__ import annotations

from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 16 has ``gen_random_uuid()`` in core (moved out of pgcrypto in PG 13),
    # so no extension is required for the default.
    op.execute(
        """
        CREATE TABLE interactions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind          TEXT NOT NULL,
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            actors        JSONB,
            subjects      JSONB,
            content       JSONB,
            source        TEXT,
            draft_id      BIGINT,
            derived_from  UUID REFERENCES interactions(id) ON DELETE SET NULL,
            provenance    JSONB,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    # GIN index on subjects so "all interactions touching account 17" is fast:
    #   WHERE subjects @> '[{"module":"accounts","id":17}]'
    op.execute(
        "CREATE INDEX idx_interactions_subjects "
        "ON interactions USING gin (subjects);"
    )
    # Chronological scans for feeds / timelines.
    op.execute(
        "CREATE INDEX idx_interactions_occurred "
        "ON interactions (occurred_at DESC);"
    )
    # Filtered scans by source (``agent:<profile>:<agent_name>`` vs manual).
    op.execute(
        "CREATE INDEX idx_interactions_source "
        "ON interactions (source, occurred_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interactions_source;")
    op.execute("DROP INDEX IF EXISTS idx_interactions_occurred;")
    op.execute("DROP INDEX IF EXISTS idx_interactions_subjects;")
    op.execute("DROP TABLE IF EXISTS interactions;")
