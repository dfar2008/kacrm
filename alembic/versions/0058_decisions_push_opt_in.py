"""Decisions daily-push: per-user opt-in column.

Adds ``decisions_daily_push_enabled`` to ``user_channel_prefs``. Tri-state
to distinguish "user hasn't been asked yet" from "user said yes" or
"user said no":

  NULL   — unprompted: SPA shows first-visit modal; backend treats as
           opt-IN by default for managers (consistent with spec §六·d
           "decisions.daily_push.enabled default true (管理类用户)").
  TRUE   — user explicitly opted in.
  FALSE  — user explicitly opted out: ``_decisions_push_disabled``
           short-circuits without LLM call or IM send.

Independence from ``push_today_priority``: the latter is the auto-drive
master toggle (§auto-drive runbook §已知 limitations 1). We keep both
columns so future v2 can split them cleanly without further migration.

Revision ID: 0058
Revises: 0057
Create Date: 2026-05-10  (renumbered 2026-05-11 to chain after dashboards 0057)
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa


revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent ADD COLUMN — the original 0056 (now renumbered 0058)
    # may have been applied earlier on a developer DB; use raw SQL with
    # IF NOT EXISTS rather than op.add_column.
    op.execute(
        "ALTER TABLE user_channel_prefs "
        "ADD COLUMN IF NOT EXISTS decisions_daily_push_enabled BOOLEAN"
    )


def downgrade() -> None:
    op.drop_column("user_channel_prefs", "decisions_daily_push_enabled")
