"""rl_agent_drafts: source-record trace columns

Phase 2 of production-activation roadmap. Adds three nullable columns
to rl_agent_drafts so each specialist draft can be linked back to:
  - the CRM record that triggered it (source_module + source_record_id)
  - the inbound message that triggered it (source_interaction_id)

Also adds a partial reverse-link array on interactions for "what drafts
did this interaction spawn".

See docs/2026-04-26-specialist-runtime-production-readiness.md Phase 2.

Revision ID: 0040
Revises: 0039
"""
from alembic import op


revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rl_agent_drafts "
        "ADD COLUMN source_module TEXT, "
        "ADD COLUMN source_record_id BIGINT, "
        "ADD COLUMN source_interaction_id UUID"
    )
    op.execute(
        "CREATE INDEX idx_rl_agent_drafts_source_record "
        "ON rl_agent_drafts (source_module, source_record_id) "
        "WHERE source_record_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_rl_agent_drafts_source_interaction "
        "ON rl_agent_drafts (source_interaction_id) "
        "WHERE source_interaction_id IS NOT NULL"
    )

    # Reverse-link: interactions table gains triggered_drafts array.
    # GIN index for "what interaction triggered draft #N" reverse lookup.
    op.execute(
        "ALTER TABLE interactions "
        "ADD COLUMN triggered_drafts BIGINT[]"
    )
    op.execute(
        "CREATE INDEX idx_interactions_triggered_drafts "
        "ON interactions USING GIN (triggered_drafts) "
        "WHERE triggered_drafts IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interactions_triggered_drafts")
    op.execute("ALTER TABLE interactions DROP COLUMN IF EXISTS triggered_drafts")
    op.execute("DROP INDEX IF EXISTS idx_rl_agent_drafts_source_interaction")
    op.execute("DROP INDEX IF EXISTS idx_rl_agent_drafts_source_record")
    op.execute(
        "ALTER TABLE rl_agent_drafts "
        "DROP COLUMN IF EXISTS source_interaction_id, "
        "DROP COLUMN IF EXISTS source_record_id, "
        "DROP COLUMN IF EXISTS source_module"
    )
