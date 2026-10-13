"""Module archive — durable snapshot taken before a custom module is deprovisioned.

deprovision_module() drops the module's physical table(s) and cascades away its
collections row + fields + permissions + changelog. Since that is irreversible
and the codebase previously had no module-delete path at all, we snapshot the
WHOLE module (collections row, field defs, every data row, subdetails, any stock
residue) into this table first. restore_module() rebuilds from the payload.

One row per deprovision event. payload is a self-contained JSON document.

Revision ID: 0103
Revises: 0102
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op


revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS module_archive (
            id           BIGSERIAL PRIMARY KEY,
            module_name  VARCHAR(255) NOT NULL,
            title        VARCHAR(255),
            title_en     VARCHAR(255),
            archived_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor_id     BIGINT,
            actor_label  VARCHAR(255),
            reason       TEXT,
            restored_at  TIMESTAMPTZ,
            payload      JSONB NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_module_archive_name "
        "ON module_archive(module_name)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS module_archive")
