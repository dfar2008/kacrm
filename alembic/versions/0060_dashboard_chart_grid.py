"""Add grid_w / grid_h to dashboard_charts.

Each chart card now occupies a configurable grid footprint:
  * ``grid_w`` ∈ [1, 4] — column span (1 = a quarter row, 4 = full row)
  * ``grid_h`` ∈ [1, 4] — row span (1 = compact, 2 = default chart card)

Defaults preserve the current visual: metric cards 1×1, viz cards 2×2.
The dashboards page reads these to lay out the CSS grid; chat ops + a
new PATCH endpoint persist resize.

Revision ID: 0060
Revises: 0059
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op


revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE dashboard_charts
          ADD COLUMN IF NOT EXISTS grid_w SMALLINT NOT NULL DEFAULT 2
            CHECK (grid_w BETWEEN 1 AND 4),
          ADD COLUMN IF NOT EXISTS grid_h SMALLINT NOT NULL DEFAULT 2
            CHECK (grid_h BETWEEN 1 AND 4);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE dashboard_charts
          DROP COLUMN IF EXISTS grid_w,
          DROP COLUMN IF EXISTS grid_h;
        """
    )
