"""Allow ``op='reorder_options'`` in schema_changelog.

Picklist option ordering is now admin-editable (drag / arrow buttons in
``FieldProperties.tsx``). Each reorder writes a ``reorder_options``
audit row through the same ``SchemaChangelogDb`` path the other
field-option ops use; without this CHECK extension the INSERT would
explode.

Revision ID: 0074
Revises: 0074
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE schema_changelog "
        "DROP CONSTRAINT IF EXISTS schema_changelog_op_check"
    )
    op.execute(
        "ALTER TABLE schema_changelog "
        "ADD CONSTRAINT schema_changelog_op_check "
        "CHECK (op = ANY (ARRAY["
        "'add_field', 'update_field', 'hide_field', "
        "'add_option', 'hide_option', 'reorder_fields', "
        "'delete_option', 'rename_option_label', "
        "'reorder_options'"
        "]::text[]))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE schema_changelog "
        "DROP CONSTRAINT IF EXISTS schema_changelog_op_check"
    )
    op.execute(
        "ALTER TABLE schema_changelog "
        "ADD CONSTRAINT schema_changelog_op_check "
        "CHECK (op = ANY (ARRAY["
        "'add_field', 'update_field', 'hide_field', "
        "'add_option', 'hide_option', 'reorder_fields', "
        "'delete_option', 'rename_option_label'"
        "]::text[]))"
    )
