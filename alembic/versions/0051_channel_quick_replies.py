"""channel_quick_replies — numeric-slot mapping for IM quick-actions

Phase 4 One-Agent Unification §3.3. When cloud pushes a card with
"回复 1 / 2 / 3" actions (or 飞书 inline buttons rendered as numbered
fallback for iLink), each slot maps to a payload — either an
``approve action_id`` or a ``copilot prompt text``. TTL 1 hour:

* Long enough for users to glance at 飞书 and reply within reasonable
  delay.
* Short enough that yesterday's "1" can't accidentally consume today's
  "1" — guard against off-by-one if a user's reply is delayed.

Lookups always go through ``(user_id, channel, slot)`` filtered by
``consumed_at IS NULL AND expires_at > now()`` — the partial index
``channel_quick_replies_lookup`` covers this exactly.

Revision ID: 0051
Revises: 0050
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_quick_replies",
        sa.Column(
            "id", sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        # {"kind":"approve","action_id":"abc12345"} or
        # {"kind":"copilot_prompt","text":"..."} or
        # {"kind":"cancel","action_id":"abc12345"} etc.
        sa.Column(
            "payload", postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "expires_at", sa.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "consumed_at", sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    # Partial index — only unconsumed rows are in scope for lookup;
    # consumed history is keep-or-prune separately.
    op.create_index(
        "channel_quick_replies_lookup",
        "channel_quick_replies",
        ["user_id", "channel", "slot", "expires_at"],
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "channel_quick_replies_lookup",
        table_name="channel_quick_replies",
    )
    op.drop_table("channel_quick_replies")
