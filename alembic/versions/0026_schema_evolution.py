"""schema_changelog + field_proposals — AI-native schema evolution substrate.

Charter §168 (new species ≠ old CRM) rejects the "seed once, freeze forever"
mental model that ``module_fields`` inherited from kacrm. See ADR
``docs/2026-04-20-schema-evolution-adr.md`` for the full reasoning.

This migration lays the audit + proposal tables that the runtime routes
and the copilot ``add_picklist_option`` tool both write into. The existing
``module_fields`` table is unchanged — mutation goes through the gateway
and is paired with a ``schema_changelog`` row in the same transaction.

Revision ID: 0026
Revises: 0025
"""

from alembic import op


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # schema_changelog — every add/hide against module_fields, regardless
    # of actor. The ``before_json`` / ``after_json`` pair makes each row
    # individually reversible without touching the main table's triggers.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_changelog (
            id           BIGSERIAL PRIMARY KEY,
            ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            actor_type   TEXT NOT NULL CHECK (actor_type IN ('user','agent')),
            actor_id     INTEGER,
            actor_label  TEXT,
            module_id    INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            module_name  TEXT NOT NULL,
            field_id     INTEGER,
            field_name   TEXT,
            op           TEXT NOT NULL CHECK (op IN (
                             'add_field','update_field','hide_field',
                             'add_option','hide_option'
                         )),
            before_json  JSONB,
            after_json   JSONB,
            reason       TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_schema_changelog_module_ts "
        "ON schema_changelog (module_id, ts DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_schema_changelog_actor "
        "ON schema_changelog (actor_type, actor_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_schema_changelog_op_ts "
        "ON schema_changelog (op, ts DESC)"
    )

    # field_proposals — agent-originated field additions wait here for
    # admin review. ``payload`` is the full draft so the reviewer can
    # eyeball exactly what would be inserted.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS field_proposals (
            id                BIGSERIAL PRIMARY KEY,
            ts                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            proposer_type     TEXT NOT NULL CHECK (proposer_type IN ('user','agent')),
            proposer_id       INTEGER,
            proposer_label    TEXT,
            module_id         INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            module_name       TEXT NOT NULL,
            payload           JSONB NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                                 'pending','approved','rejected','withdrawn'
                              )),
            reviewer_id       INTEGER,
            review_ts         TIMESTAMPTZ,
            review_note       TEXT,
            created_field_id  INTEGER
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_field_proposals_status_ts "
        "ON field_proposals (status, ts DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_field_proposals_module "
        "ON field_proposals (module_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS field_proposals")
    op.execute("DROP TABLE IF EXISTS schema_changelog")
