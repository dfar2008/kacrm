"""Add ``source_skill`` column to ``trust_levels``.

Companion to 0092 (which added the same column to ``gateway_actions``).
Same root cause: an ad-hoc ALTER ran on the local DB at some point past
without landing as an alembic migration, so the column existed locally
but never on fresh deployments (prod 203.0.113.10).

``hermes/trust/engine.py`` looks up trust by the tuple
``(profile_id, action_kind, source_skill)`` (see lines 87-104). With
the column missing, every trust query at the dispatcher layer crashes
with ``column "source_skill" does not exist``, and the agent ends up
falling back to bypassing the dispatcher entirely (losing the trust
chain).

Idempotent: ``IF NOT EXISTS``.

Revision ID: 0093
Revises: 0092
Create Date: 2026-05-30
"""
from __future__ import annotations

from alembic import op


revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trust_levels "
        "ADD COLUMN IF NOT EXISTS source_skill TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE trust_levels "
        "DROP COLUMN IF EXISTS source_skill"
    )
