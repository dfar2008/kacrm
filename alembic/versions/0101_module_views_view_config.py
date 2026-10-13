"""Add ``view_config`` to ``module_views`` and allow the ``timeline`` type.

Spec 3 — a saved view can now be one of list / card / kanban / timeline /
calendar. Type-specific settings (kanban group field, calendar/timeline
date field, card fields, etc.) live in a per-view ``view_config JSONB``.

Two changes:
  1. ``view_config JSONB`` — type-specific config, NULL = none.
  2. Widen the ``type`` CHECK to include ``timeline`` (0003 created it with
     table/card/kanban/chart/calendar only).

Revision ID: 0101
Revises: 0100
Create Date: 2026-06-16
"""
from __future__ import annotations

from alembic import op


revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE module_views
            ADD COLUMN IF NOT EXISTS view_config JSONB
        """
    )
    op.execute(
        "ALTER TABLE module_views DROP CONSTRAINT IF EXISTS module_views_type_check"
    )
    op.execute(
        """
        ALTER TABLE module_views
            ADD CONSTRAINT module_views_type_check
            CHECK (type IN ('table','card','kanban','chart','calendar','timeline'))
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE module_views DROP CONSTRAINT IF EXISTS module_views_type_check"
    )
    op.execute(
        """
        ALTER TABLE module_views
            ADD CONSTRAINT module_views_type_check
            CHECK (type IN ('table','card','kanban','chart','calendar'))
        """
    )
    op.execute("ALTER TABLE module_views DROP COLUMN IF EXISTS view_config")
