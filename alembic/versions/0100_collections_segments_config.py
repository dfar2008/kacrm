"""Add ``segments_config`` to ``collections`` and clear seeded views.

Two changes, both serving the "list-top views" feature (Spec 2):

1. ``segments_config JSONB`` — per-module override for the built-in
   event-driven segment tabs (today / overdue / new / dormant / all):
   which are enabled, their order, display label, and thresholds
   (``new.days`` / ``dormant.days``). NULL means "use built-in defaults".
   The segment *kinds* stay fixed (5 ids); only their presentation and
   thresholds are configurable.

2. ``DELETE FROM module_views`` — ``seed_metadata.py`` seeds four views
   per module (All / My / My Created / Team Records) whose ``filters``
   were never actually applied and which the SPA never surfaced. As we
   now (a) make filters take effect and (b) render views in the UI, these
   latent rows would suddenly appear. Per product decision the custom-view
   list starts empty, so we clear them here. The seed itself is also
   neutered (DEFAULT_VIEWS=[]) so fresh installs don't regrow them.
   ``module_view_fields`` cascade-deletes via FK.

Downgrade restores neither the column-drop's data nor the deleted views
(they were unused seed data — acceptable loss).

Revision ID: 0100
Revises: 0099
Create Date: 2026-06-16
"""
from __future__ import annotations

from alembic import op


revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE collections
            ADD COLUMN IF NOT EXISTS segments_config JSONB
        """
    )
    # ``filter_logic`` — how a view's filter conditions combine ('all' = AND,
    # 'any' = OR). ListQuery.set_filters already accepts this; the table just
    # had nowhere to persist it. Default 'all' matches set_filters' default.
    op.execute(
        """
        ALTER TABLE module_views
            ADD COLUMN IF NOT EXISTS filter_logic VARCHAR(8) NOT NULL
            DEFAULT 'all'
        """
    )
    # Clear latent seeded views — see module docstring.
    op.execute("DELETE FROM module_views")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE collections DROP COLUMN IF EXISTS segments_config"
    )
    op.execute("ALTER TABLE module_views DROP COLUMN IF EXISTS filter_logic")
