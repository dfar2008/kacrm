"""agent_tasks: specialist-emitted task module

Phase 4.0c — Coach (and other specialists) propose follow-up tasks
that are auto-executed via the trust engine (``task.create=3`` →
auto on low/medium risk). The dashboard surfaces them as a list with
mark-done / 撤销 affordances.

Soft-delete only: revert flips ``deleted=true``, history is preserved
for audit.

Revision ID: 0041
Revises: 0040
"""
from alembic import op


revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_tasks (
            id                 BIGSERIAL PRIMARY KEY,
            profile_id         TEXT NOT NULL,
            owner_user_id      BIGINT,
            title              TEXT NOT NULL,
            description        TEXT,
            due_at             TIMESTAMPTZ,
            status             TEXT NOT NULL DEFAULT 'open',
            related_module     TEXT,
            related_record_id  BIGINT,
            source_agent       TEXT,
            source_draft_id    BIGINT,
            gateway_action_id  UUID,
            meta               JSONB NOT NULL DEFAULT '{}'::jsonb,
            deleted            BOOLEAN NOT NULL DEFAULT false,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_tasks_owner_open "
        "ON agent_tasks (profile_id, owner_user_id, status) "
        "WHERE deleted = false"
    )
    op.execute(
        "CREATE INDEX idx_agent_tasks_related "
        "ON agent_tasks (related_module, related_record_id) "
        "WHERE deleted = false"
    )
    op.execute(
        "CREATE INDEX idx_agent_tasks_action "
        "ON agent_tasks (gateway_action_id) "
        "WHERE gateway_action_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_tasks_action")
    op.execute("DROP INDEX IF EXISTS idx_agent_tasks_related")
    op.execute("DROP INDEX IF EXISTS idx_agent_tasks_owner_open")
    op.execute("DROP TABLE IF EXISTS agent_tasks")
