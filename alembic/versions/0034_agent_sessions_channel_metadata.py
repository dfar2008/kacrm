"""agent_sessions: cross-channel metadata for memory queries

Charter §一原语 ② Memory Substrate — to query "what did this user say
on the *group* chat about pricing?" we need ``chat_type`` (dm vs
group), ``thread_id`` (slack threads, feishu reply chains), and
``chat_name`` (room/group display name) on the session row. Hermes
upstream's Hindsight memory plugin gained richer session metadata
in PR #b66644f0 — same insight applies to our PG-backed Memory.

These columns are nullable and additive; existing rows stay valid.
PgSessionDB writes them on ``create_session`` when the bridge passes
them in (separate PR will thread the args through). For now the
columns just exist so SQL queries can JOIN against them without
needing JSONB extracts.

Revision ID: 0034
Revises: 0033
"""

from alembic import op


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_sessions "
        "ADD COLUMN chat_type TEXT, "
        "ADD COLUMN chat_name TEXT, "
        "ADD COLUMN thread_id TEXT"
    )
    # Index for the "all sessions in this group chat" memory query.
    op.execute(
        "CREATE INDEX idx_agent_sessions_chat_lookup "
        "ON agent_sessions (chat_type, source) "
        "WHERE chat_type IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_sessions_chat_lookup")
    op.execute(
        "ALTER TABLE agent_sessions "
        "DROP COLUMN IF EXISTS thread_id, "
        "DROP COLUMN IF EXISTS chat_name, "
        "DROP COLUMN IF EXISTS chat_type"
    )
