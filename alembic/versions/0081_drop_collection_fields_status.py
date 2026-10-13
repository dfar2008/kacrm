"""Drop the orphan ``collection_fields`` row for ``potentials.status``.

alembic 0072 (2026-05-25) removed the ``module_fields`` row for
``potentials.status`` when it merged status into milestone, but the
parallel ``collection_fields`` form-layer row was left behind. SPA
form renderers can read from either table; the orphan row was still
showing the old "状态" picklist with stale options
``[new, negotiation, proposal, won, lost]`` on the商机 form.

This migration deletes that row. If anyone re-runs seed_metadata.py,
the ``_upsert_collection_field`` loop won't recreate ``status``
because ``seed_metadata`` no longer declares it.

Revision ID: 0081
Revises: 0080
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM collection_fields
         WHERE collection_name = 'potentials'
           AND name = 'status'
        """
    )


def downgrade() -> None:
    # No-op: the field has been gone from seed_metadata since 0072 and
    # there's no useful state to restore (column was dropped by an
    # earlier hand migration, kacrm-era options long obsolete).
    pass
