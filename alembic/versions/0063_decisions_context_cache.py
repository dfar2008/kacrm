"""decisions_context_cache — per-user TTL'd cache of agent-generated
"current context" signals for the decisions surface.

Charter v2 §定律 4: the 5 hardcoded contextual rules in
``prompt_suggestions.py`` are replaced by the ``crm-decisions-context``
skill. The agent run takes ~3-4 minutes; we cache its output here so
the SPA loads instantly. TTL = 4 hours (refresh ~3-4 times per
workday); a manual refresh button in the SPA bypasses the cache.

Migration 0062 (``users.decisions_rule_thresholds``) is now dead
weight — no rules to threshold. Left in place so an in-flight rollback
doesn't lose the column; a follow-up cleanup can drop it.

Revision ID: 0063
Revises: 0062
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decisions_context_cache",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False,
                  server_default=sa.text("'team'")),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'[]'::jsonb")),
        # raw_reply preserved for debugging — what the LLM actually
        # returned before parsing. Capped via JSONB column type.
        sa.Column("raw_reply", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "scope"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_decisions_context_cache_computed",
        "decisions_context_cache",
        ["computed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_decisions_context_cache_computed",
                  table_name="decisions_context_cache")
    op.drop_table("decisions_context_cache")
