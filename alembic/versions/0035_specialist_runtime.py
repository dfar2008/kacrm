"""v2.3 specialist runtime — gateway_actions specialist columns,
delegation ACL / edges tables, memories shadow column.

Charter §一原语 ④ Agent Pool + ⑤ Action Gateway + ⑥ Feedback Collector.
See docs/2026-04-26-v23-specialist-runtime-design.md §2.5.D + §2.7.

The schema is forward-only safe: every new column is nullable, every
new table is empty on creation, and no constraints are tightened on
existing rows. The runtime backfills these columns lazily.

Revision ID: 0035
Revises: 0034
"""

from alembic import op


revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gateway_actions: who actually executed the action (executing_specialist
    # may differ from the originating agent), plus delegation lineage
    # (parent / root draft IDs) and a typed reject reason for the feedback
    # collector to bucket on.
    op.execute(
        "ALTER TABLE gateway_actions "
        "ADD COLUMN executing_specialist TEXT, "
        "ADD COLUMN delegation_parent_draft_id BIGINT, "
        "ADD COLUMN delegation_root_draft_id BIGINT, "
        "ADD COLUMN reject_reason_type TEXT"
    )
    op.execute(
        "CREATE INDEX idx_gateway_actions_executing_specialist "
        "ON gateway_actions (executing_specialist) "
        "WHERE executing_specialist IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_gateway_actions_delegation_root "
        "ON gateway_actions (delegation_root_draft_id) "
        "WHERE delegation_root_draft_id IS NOT NULL"
    )

    # ACL: which specialists may delegate which action kinds to which
    # children. Composite PK = (parent, child).
    op.execute(
        "CREATE TABLE specialist_delegation_acl ("
        "  parent_specialist TEXT NOT NULL, "
        "  child_specialist  TEXT NOT NULL, "
        "  allowed_action_kinds JSONB NOT NULL DEFAULT '[]'::jsonb, "
        "  PRIMARY KEY (parent_specialist, child_specialist)"
        ")"
    )

    # Edges: actual delegation events at runtime — used by the feedback
    # collector to attribute rewards along delegation chains.
    op.execute(
        "CREATE TABLE specialist_delegation_edges ("
        "  id BIGSERIAL PRIMARY KEY, "
        "  parent_draft_id BIGINT, "
        "  child_draft_id  BIGINT, "
        "  parent_specialist TEXT NOT NULL, "
        "  child_specialist  TEXT NOT NULL, "
        "  edge_outcome TEXT, "
        "  created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX idx_delegation_edges_parent_draft "
        "ON specialist_delegation_edges (parent_draft_id) "
        "WHERE parent_draft_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_delegation_edges_child_draft "
        "ON specialist_delegation_edges (child_draft_id) "
        "WHERE child_draft_id IS NOT NULL"
    )

    # Shadow column for memory redaction — the runtime writes the redacted
    # form here while ``content`` keeps the original. Nullable; backfill is
    # opportunistic.
    op.execute("ALTER TABLE memories ADD COLUMN content_redacted TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS content_redacted")
    op.execute("DROP TABLE IF EXISTS specialist_delegation_edges")
    op.execute("DROP TABLE IF EXISTS specialist_delegation_acl")
    op.execute("DROP INDEX IF EXISTS idx_gateway_actions_delegation_root")
    op.execute("DROP INDEX IF EXISTS idx_gateway_actions_executing_specialist")
    op.execute(
        "ALTER TABLE gateway_actions "
        "DROP COLUMN IF EXISTS reject_reason_type, "
        "DROP COLUMN IF EXISTS delegation_root_draft_id, "
        "DROP COLUMN IF EXISTS delegation_parent_draft_id, "
        "DROP COLUMN IF EXISTS executing_specialist"
    )
