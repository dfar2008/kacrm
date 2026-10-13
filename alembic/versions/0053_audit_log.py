"""audit_log — Phase 5.5 (plan v1.2 §4.6 / §5)

Records every team-scope or org-scope read so a manager's access to
subordinate data can be audited later. Per plan v1.2 §3 + §9:

* Stores ``(caller_id, scope_kind, resource_kind, resource_filter::jsonb,
  occurred_at)`` — explicitly NOT ``affected_user_ids[]`` (would
  bloat for org scope; replay-from-filter instead).
* BRIN index on ``(caller_id, occurred_at)`` — append-heavy /
  range-query / point-query rare.
* Retention 90 days; monthly cron archives older rows to S3 then
  DELETEs (cron lives separately, see Phase 5.5 Day 4).

Revision ID: 0053
Revises: 0052
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column(
            "id", sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("caller_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "scope_kind", sa.Text(), nullable=False,
        ),
        sa.Column(
            "resource_kind", sa.Text(), nullable=False,
        ),
        sa.Column(
            "resource_filter", postgresql.JSONB(), nullable=True,
        ),
        sa.Column(
            "occurred_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "scope_kind IN ('self','team','org')",
            name="audit_log_scope_kind_chk",
        ),
        sa.ForeignKeyConstraint(
            ["caller_id"], ["users.id"], ondelete="CASCADE",
        ),
    )
    # BRIN index (append-heavy table, range queries dominate).
    op.execute(
        "CREATE INDEX audit_log_caller_time "
        "ON audit_log USING BRIN (caller_id, occurred_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS audit_log_caller_time")
    op.drop_table("audit_log")
