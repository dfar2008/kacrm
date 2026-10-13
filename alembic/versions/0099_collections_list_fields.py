"""Add ``list_fields`` to ``collections`` — per-module list-item field
customization (two-row layout).

Sibling of ``headline_fields`` (migration 0020). Where ``headline_fields``
curates the RecordDetail chip-row, ``list_fields`` curates which fields
each row in the left-hand record *list* shows, split across two rows.

Column shape
------------
``list_fields JSONB`` — object ``{"row1": [field_name, ...],
"row2": [field_name, ...]}``. Each entry is a ``module_fields.field_name``
on the module's record table. NULL means "use the frontend default."
Field count is intentionally *not* capped (unlike headline's ≤5) — the
write endpoint validates names exist but lets the operator pack as many
as they want; the list item wraps row2 within its line.

Personal per-user overrides live in the browser (localStorage); this
column is the org-wide default an admin sets.

Revision ID: 0099
Revises: 0098
Create Date: 2026-06-16
"""
from __future__ import annotations

from alembic import op


revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE collections
            ADD COLUMN IF NOT EXISTS list_fields JSONB
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE collections DROP COLUMN IF EXISTS list_fields")
