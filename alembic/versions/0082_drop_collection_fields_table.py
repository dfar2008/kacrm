"""Drop the kacrm-era ``collection_fields`` zombie table.

History
-------
``collection_fields`` is form-layer metadata inherited from kacrm:
``(collection_name, name, extra::jsonb)`` with ``extra`` carrying the
widget hint + picklist options.

By the time the hermes port matured, every CRM read path (list_query,
detail_query, EditQuery, Save, agent ``crm_*`` tools, frontend Schema
admin) had moved to ``module_fields`` — which is a strict superset of
the same data plus normalized columns (``field_type``, ``is_required``,
``field_config``, …).

The only writer left was ``seed_metadata._upsert_collection_field``;
the only reader was the gateway class
``hermes/crm/models/collection_fields_db.py`` (no callers anywhere).
Verified via repo-wide grep prior to drop.

Because nothing read it, schema changes never propagated. Today's
"商机.status 死灰复燃" was caused by an orphan row in this table —
``alembic 0072`` cleaned ``module_fields.status`` but left the
``collection_fields`` row, and SPA form code briefly read from there as
a fallback. ``alembic 0081`` patched the one orphan; this migration
finishes the job by removing the table entirely.

What
----
DROP TABLE collection_fields CASCADE. The companion ``ff124ba``
commit removed the seed writer + gateway class so re-deploy doesn't
re-create writes that error out.

Revision ID: 0082
Revises: 0081
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS collection_fields CASCADE")


def downgrade() -> None:
    # Recreate the empty shell so anyone who downgrades for a different
    # reason doesn't trip a foreign-key / view dependency they forgot
    # about. The data itself is unrecoverable (and was stale anyway).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_fields (
            id              BIGSERIAL PRIMARY KEY,
            collection_name VARCHAR(64)  NOT NULL,
            name            VARCHAR(64)  NOT NULL,
            extra           JSONB,
            UNIQUE (collection_name, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_fields_by_collection "
        "ON collection_fields(collection_name)"
    )
