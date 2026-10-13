"""agent_messages.codex_message_items — mirror hermes v11 schema bump

Hermes upstream SCHEMA_VERSION 8 → 11 (v2026.4.23 → v2026.5.7) added
``codex_message_items TEXT`` to the sqlite ``messages`` table to carry
codex-format response items (parallel to ``codex_reasoning_items``).
PgSessionDB needs the same column on ``agent_messages`` so PG mode
behaves identically — without this column, hermes' own
``test_codex_message_items_persisted_and_restored`` (and any cloud
consumer that reads codex assistant turns from PG) breaks.

JSONB rather than TEXT: the field is always a serialized list/dict in
practice, and JSONB lets us index inside it later if a use case appears.
sqlite stores it as TEXT JSON — round-trips work either way.

Revision ID: 0048
Revises: 0047
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_messages",
        sa.Column("codex_message_items", sa.dialects.postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_messages", "codex_message_items")
