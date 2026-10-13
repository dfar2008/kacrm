"""Backfill: flip stale pending_approval rows of now-auto-execute kinds.

Charter §11 redo: ``crm.write``, ``task.create``, ``coach.proposal``,
``memory.write``, ``lead.convert`` no longer require human approval —
the dispatcher's ``_FORCE_AUTO_EXECUTE_KINDS`` runs them on submit. Any
historical rows still sitting in ``pending_approval`` are vestigial UI
clutter (the approve button does nothing semantically meaningful now);
mark them ``rejected`` so they drop out of every state filter.

We pick ``rejected`` (not ``executed``) on purpose: those rows were
never actually run by an executor, so claiming ``executed`` would lie
to the audit trail. ``rejected`` honestly says "decision: do nothing".

Revision ID: 0071
Revises: 0070
Create Date: 2026-05-25
"""
from __future__ import annotations

from alembic import op


revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


_AUTO_EXEC_KINDS = (
    "crm.write", "task.create", "coach.proposal",
    "memory.write", "lead.convert",
)


def upgrade() -> None:
    placeholders = ", ".join(f"'{k}'" for k in _AUTO_EXEC_KINDS)
    op.execute(
        f"""
        UPDATE gateway_actions
        SET state = 'rejected'
        WHERE state = 'pending_approval'
          AND kind IN ({placeholders})
        """
    )


def downgrade() -> None:
    # Irreversible: we can't tell which rejected rows came from this
    # backfill vs. genuine cancellations. Leave them as-is on downgrade.
    pass
