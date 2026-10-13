"""Drop decisions surface artifacts — consolidation Phase 2.D.

Removes the storage side of the deleted ``/app/decisions`` surface:

* ``decisions_context_cache`` table (migration 0063) — per-user TTL'd
  agent-generated signal cache. Decisions page is gone; the
  ``crm-decisions-context`` skill is gone; nothing reads/writes this.
* ``users.decisions_rule_thresholds`` JSONB column (migration 0062) —
  per-rule trigger threshold override for the 5 hardcoded contextual
  rules in ``prompt_suggestions.py``. Those rules and the rule-tuning
  endpoint (PUT /api/decisions/rule_thresholds) are deleted; the column
  is unread/unwritten now.

Other potential decisions tables (briefing_cache_today, decision_
messages, decision_threads) were never actually created in this
deployment (queried pg_tables on 2026-05-17, only decisions_context
_cache existed). The downgrade restores both artifacts so a rollback
of this revision leaves the schema usable for a re-deploy of the
decisions module.

See docs/2026-05-16-decisions-coach-autodrive-consolidation.md Phase 2.

Revision ID: 0065
Revises: 0064
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "idx_decisions_context_cache_computed",
        table_name="decisions_context_cache",
        if_exists=True,
    )
    op.drop_table("decisions_context_cache")
    op.drop_column("users", "decisions_rule_thresholds")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "decisions_rule_thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "decisions_context_cache",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False,
                  server_default=sa.text("'team'")),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_reply", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "scope"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_decisions_context_cache_computed",
        "decisions_context_cache",
        ["computed_at"],
    )
