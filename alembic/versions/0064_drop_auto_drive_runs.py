"""Drop auto_drive_runs — auto_drive engine removed (consolidation
Phase 0).

The ``hermes_cloud/app/agent/auto_drive.py`` module + ``auto_drive_daily``
celery task + all SPA / push integration are gone. See
docs/2026-05-16-decisions-coach-autodrive-consolidation.md Phase 0.

Historical ``interactions(kind='auto_drive.*')`` rows are PRESERVED
(SoT is immutable). Only the auxiliary ``auto_drive_runs`` cron-state
table is dropped here. Downgrade recreates the schema from 0052 so
rollback of this revision restores the table empty.

Revision ID: 0064
Revises: 0063
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("auto_drive_runs")


def downgrade() -> None:
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
        sa.Column("signals_seen", sa.Integer(), nullable=True),
        sa.Column("actions_taken", sa.Integer(), nullable=True),
        sa.Column("skipped_reason", sa.Text(), nullable=True),
        sa.Column("interaction_id", sa.dialects.postgresql.UUID(),
                  nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_auto_drive_runs_user_started",
        "auto_drive_runs",
        ["user_id", sa.text("started_at DESC")],
    )
