"""RL field changelog — per-field audit with agent attribution.

Plan section 4.4. Unlike the kacrm ``changelog`` table (UI-facing audit log),
this table records **who** changed each field with structured attribution
(``u:<user_id>`` vs ``a:<profile_id>:<agent_name>``) and links back to
``rl_agent_drafts`` and ``interactions``. The RL reward function uses this
to measure per-field accuracy of agent predictions.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-16
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rl_field_changelog (
            id              BIGSERIAL PRIMARY KEY,
            module          TEXT NOT NULL,
            record_id       BIGINT NOT NULL,
            field_name      TEXT NOT NULL,
            old_value       JSONB,
            new_value       JSONB,
            changed_by      VARCHAR(64) NOT NULL,
            change_source   TEXT NOT NULL DEFAULT 'manual',
            agent_draft_id  BIGINT REFERENCES rl_agent_drafts(id) ON DELETE SET NULL,
            interaction_id  UUID REFERENCES interactions(id) ON DELETE SET NULL,
            changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_rl_fc_module_record "
        "ON rl_field_changelog (module, record_id, changed_at DESC);"
    )
    op.execute(
        "CREATE INDEX idx_rl_fc_draft "
        "ON rl_field_changelog (agent_draft_id) "
        "WHERE agent_draft_id IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX idx_rl_fc_changed_by "
        "ON rl_field_changelog (changed_by, changed_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_rl_fc_changed_by;")
    op.execute("DROP INDEX IF EXISTS idx_rl_fc_draft;")
    op.execute("DROP INDEX IF EXISTS idx_rl_fc_module_record;")
    op.execute("DROP TABLE IF EXISTS rl_field_changelog;")
