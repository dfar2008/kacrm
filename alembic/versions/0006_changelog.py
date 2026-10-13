"""Changelog table — per-(module, record, field) audit log.

kacrm writes a ``changelog`` row every time a record field changes (including
``deleted`` flips on soft-delete). ItemDelete and SaveBase both depend on
``ChangelogDb.log_field_change``. Porting those verbatim requires this table
to exist.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-16
"""
from __future__ import annotations

from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``tracking_id`` stays free-text (VARCHAR) — kacrm uses it as an opaque
    # correlation key so one "save" call can fan out multiple field-change
    # rows with the same id. ``updated_at`` is TIMESTAMPTZ per our schema
    # convention. We intentionally don't FK ``data_id`` — the referenced
    # table is dynamic (one of accounts/contacts/leads/...) so the enforcement
    # happens at the application layer.
    op.execute(
        """
        CREATE TABLE changelog (
            tracking_id   BIGSERIAL PRIMARY KEY,
            module_id     BIGINT NOT NULL
                          REFERENCES collections(id) ON DELETE CASCADE,
            data_id       BIGINT NOT NULL,
            field_name    VARCHAR(100) NOT NULL,
            field_label   VARCHAR(200) NOT NULL DEFAULT '',
            field_type    VARCHAR(20)  NOT NULL DEFAULT 'text',
            old_value     TEXT,
            new_value     TEXT,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by    BIGINT REFERENCES users(id) ON DELETE SET NULL
        );
        """
    )
    # Detail page pulls changelog rows for a given (module, record) ordered
    # by ``updated_at DESC`` — mirror kacrm's btree.
    op.execute(
        "CREATE INDEX idx_changelog_module_data "
        "ON changelog(module_id, data_id, updated_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_changelog_module_data;")
    op.execute("DROP TABLE IF EXISTS changelog;")
