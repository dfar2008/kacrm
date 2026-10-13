"""user_channel_prefs — per-user IM push preferences

Phase 4 One-Agent Unification §3.1. Default-all push policy means every
user has a row (auto-INSERTed at first channel binding); fields encode
their personal opt-out / quiet hours / mute lists. Defaults are gentle
(quiet 22→8, max 3 alerts/day, only severity=high real-time alerts) so
"default-all" doesn't translate to user being spammed on day 1.

``prefer_team_brief`` is added now (default false, unread by Phase 4)
so Phase 5 manager/team view (plan §10.4) can flip the default without
an alter — re-migrating a wide row table at scale is annoying enough
that paying 1 column up-front for a known future need is right.

Revision ID: 0049
Revises: 0048
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_channel_prefs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column(
            "push_today_priority", sa.Boolean(),
            nullable=False, server_default=sa.true(),
        ),
        sa.Column(
            "push_weekly", sa.Boolean(),
            nullable=False, server_default=sa.true(),
        ),
        sa.Column(
            "push_alert_min_sev", sa.Text(),
            nullable=False, server_default="high",
        ),
        # 22 → 8 means quiet from 22:00 to 08:00 next day (wraps midnight).
        # SMALLINT 0..23.
        sa.Column(
            "quiet_hours_start", sa.SmallInteger(),
            nullable=False, server_default="22",
        ),
        sa.Column(
            "quiet_hours_end", sa.SmallInteger(),
            nullable=False, server_default="8",
        ),
        sa.Column(
            "max_per_day", sa.SmallInteger(),
            nullable=False, server_default="3",
        ),
        sa.Column(
            "timezone", sa.Text(),
            nullable=False, server_default="Asia/Shanghai",
        ),
        # JSONB array of signal names the user has muted (e.g.
        # ["industry_field_change_repeated"]). ai_noticed checks this
        # before pushing.
        sa.Column(
            "muted_signals", postgresql.JSONB(),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        # Phase 5 hook (plan §10.4) — manager/team brief opt-in.
        # Phase 4 doesn't read this; column exists so Phase 5 doesn't
        # alter at scale.
        sa.Column(
            "prefer_team_brief", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "channel"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_channel_prefs")
