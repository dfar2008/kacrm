"""channel_push_log — sidecar for IM push receipts + dedup

Phase 4 One-Agent Unification §3.2. **NOT** the source of truth for
push events — every push first emits ``kind='channel.push'`` into
``interactions`` (Charter §一定律 1 events-first). This sidecar exists
to:

* Index dedup keys (user × kind × ref_id) cheaply.
* Hold the actual rendered payload (飞书 card JSON / iLink text) for
  bridge-replay debugging.
* Track delivery receipts from the IM bridge (delivered=true once
  飞书 webhook acks).

Throttle / dedup rules (enforced in app code, not DB):

* same (user_id, kind, ref_id) within 24h → suppress (looked up via
  ``channel_push_log_dedup`` index).
* ``count(*) WHERE kind='alert' AND user_id=X >= max_per_day`` → write
  ``kind='throttled'`` row, do NOT actually push.

Revision ID: 0050
Revises: 0049
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_push_log",
        sa.Column(
            "id", sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        # Source-of-truth interaction this row mirrors. Cascade so
        # interaction deletion (rare, only test cleanup) cleans up the
        # sidecar too.
        sa.Column(
            "interaction_id", postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        # 'daily_completed' | 'weekly' | 'alert' | 'welcome' | 'throttled'
        sa.Column("kind", sa.Text(), nullable=False),
        # Business-id for dedup: record_id / signal_id / action_id.
        # Nullable because some kinds (welcome, weekly) don't dedup on
        # a single ref.
        sa.Column("ref_id", sa.Text(), nullable=True),
        # Actual outbound payload (飞书 interactive card JSON, or iLink
        # text). For replay / human inspection on push-failure tickets.
        sa.Column(
            "rendered", postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "pushed_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "delivered", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["interaction_id"], ["interactions.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
        ),
    )
    # Used by the per-day cap query: count today's alerts for user X.
    # NOTE: ``pushed_at::date`` isn't IMMUTABLE (depends on session TZ),
    # so we index ``(user_id, kind, pushed_at)`` directly and let the
    # query use a range predicate (``pushed_at >= today_start AND
    # pushed_at < tomorrow_start``). btree on timestamp serves the same
    # access pattern.
    op.create_index(
        "channel_push_log_user_kind_pushed",
        "channel_push_log",
        ["user_id", "kind", "pushed_at"],
    )
    # Used by dedup lookup: have we pushed (user, kind, ref_id) in last 24h?
    op.create_index(
        "channel_push_log_dedup",
        "channel_push_log",
        ["user_id", "kind", "ref_id"],
    )


def downgrade() -> None:
    op.drop_index("channel_push_log_dedup", table_name="channel_push_log")
    op.drop_index("channel_push_log_user_kind_pushed", table_name="channel_push_log")
    op.drop_table("channel_push_log")
