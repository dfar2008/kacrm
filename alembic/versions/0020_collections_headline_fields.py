"""Add ``headline_fields`` to ``collections`` — per-module headline
chip-row customization.

Charter §168 (新物种 ≠ 旧 CRM) — the RecordDetail chip row shows the
≤5 business-language facts pinned above the fold. Until now the picks
were hard-coded in ``frontend/src/lib/moduleHeadlines.ts`` with no way
for admins to tweak them. This migration moves that choice into the
data layer so each deployment can curate its own "first glance" lens.

Column shape
------------
``headline_fields JSONB`` — array of strings, each a ``module_fields
.field_name`` on the module's record table. NULL means "use the
frontend default." Capped at 5 entries by the write endpoint (not
DB-enforced — we want callers to get a friendly 400, not a CHECK
violation).

Only the non-internal CRM record modules make sense here; Hermes
platform collections (``analytics`` / ``approvals`` / ``chat`` / …)
have no detail view so the column is just ignored.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-18
"""
from __future__ import annotations

from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE collections
            ADD COLUMN IF NOT EXISTS headline_fields JSONB
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE collections DROP COLUMN IF EXISTS headline_fields")
