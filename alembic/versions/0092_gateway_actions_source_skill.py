"""Add ``source_skill`` column to ``gateway_actions``.

This column was added ad-hoc in local DBs (some past code path ran an
ALTER TABLE outside alembic) but never landed as a real migration, so
fresh deployments — including production at 203.0.113.10 — were missing
it. ``hermes/tools/crm/dispatch.py`` writes ``source_skill`` into the
gateway_actions row when an action originates from a known skill, and
``hermes/trust/engine.py`` keys trust resolution on the
``(profile_id, action_kind, source_skill)`` tuple, so a missing column
makes every ``crm_write`` insert fail at the DB layer until the agent
falls back to a direct write (which loses the trust/specialist link).

Idempotent: ``IF NOT EXISTS`` — local DBs that already grew the column
ad-hoc see this as a no-op, fresh DBs get it.

Revision ID: 0092
Revises: 0091
Create Date: 2026-05-30
"""
from __future__ import annotations

from alembic import op


revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gateway_actions "
        "ADD COLUMN IF NOT EXISTS source_skill TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gateway_actions "
        "DROP COLUMN IF EXISTS source_skill"
    )
