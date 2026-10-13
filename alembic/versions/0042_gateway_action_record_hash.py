"""gateway_actions: record_hash column + 'conflict' state value

Phase 4.2 — revert conflict guard. Each executor stores a stable hash
of the user-meaningful fields of the row it just wrote into
``gateway_actions.record_hash``. At revert time the executor re-reads
the row and recomputes; mismatch raises ``ConflictError`` and the
dispatcher transitions the action to ``state='conflict'`` rather than
flipping the soft-delete.

Legacy rows (NULL ``record_hash``) bypass the guard for back-compat.

Also locks down the previously free-text ``state`` column with a CHECK
constraint over the canonical set so a typo can never silently land.

Revision ID: 0042
Revises: 0041
"""
from __future__ import annotations

from alembic import op


revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


_ALLOWED_STATES = (
    "pending_approval",
    "approved",
    "blocked",
    "rejected",
    "executed",
    "reverted",
    "conflict",
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gateway_actions "
        "ADD COLUMN IF NOT EXISTS record_hash TEXT"
    )
    # Lock the state column — was free TEXT, now constrained. Existing
    # rows must already use one of the canonical values; if any legacy
    # value snuck in, surface it loudly here rather than at runtime.
    states_sql = ", ".join(f"'{s}'" for s in _ALLOWED_STATES)
    op.execute(
        "ALTER TABLE gateway_actions "
        "ADD CONSTRAINT gateway_actions_state_check "
        f"CHECK (state IN ({states_sql}))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gateway_actions "
        "DROP CONSTRAINT IF EXISTS gateway_actions_state_check"
    )
    op.execute(
        "ALTER TABLE gateway_actions DROP COLUMN IF EXISTS record_hash"
    )
