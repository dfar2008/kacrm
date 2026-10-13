"""auto_drive_runs — log of auto_drive engine ticks

Phase 4 One-Agent Unification §3.4 / §4.5. The auto_drive engine runs
at cron (typically before user wakes up) and scans ai_noticed / overdue
/ sleeping signals. For each it ALREADY does the work (drafts the
email, writes the note, schedules the task) — Charter §定律 5 "AI
drives". This row is the run-level summary; per-action artifacts live
in ``crm_drafts`` / ``gateway_actions`` / ``interactions`` (the latter
gets ``kind='auto_drive.tick'`` per run + ``kind='draft.created'`` per
action).

Idempotency is enforced at app level: one run per user per day max.

``skipped_reason`` semantics: if non-null, the run skipped doing work
(quiet hours / user_disabled / dry_run / no_signals_seen) — the row
still exists for "we did look" auditing, but actions_taken=0.

Revision ID: 0052
Revises: 0051
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auto_drive_runs",
        sa.Column(
            "id", sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "started_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "finished_at", sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        # Counts (post-finish snapshot). Pre-finish == NULL.
        sa.Column("signals_seen", sa.Integer(), nullable=True),
        sa.Column("actions_taken", sa.Integer(), nullable=True),
        # Non-null when run skipped doing work; 'quiet_hours' |
        # 'user_disabled' | 'dry_run' | 'no_signals_seen' | 'error:<msg>'.
        sa.Column("skipped_reason", sa.Text(), nullable=True),
        # The kind='auto_drive.tick' interaction this run emits.
        # Nullable because the interaction is emitted at run-end; a
        # crashed run row exists with NULL here.
        sa.Column(
            "interaction_id", postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Detailed per-signal / per-action sketch. Schema versioned
        # via payload.version.
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"], ["interactions.id"], ondelete="SET NULL",
        ),
    )
    # Lookups: "this user's runs today / this week" — keep fast.
    # Same IMMUTABLE caveat as channel_push_log_user_kind_pushed (see
    # 0050): index on raw timestamp + caller uses range predicate.
    op.create_index(
        "auto_drive_runs_user_started",
        "auto_drive_runs",
        ["user_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("auto_drive_runs_user_started", table_name="auto_drive_runs")
    op.drop_table("auto_drive_runs")
