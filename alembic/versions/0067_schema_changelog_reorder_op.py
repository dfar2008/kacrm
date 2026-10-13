"""Allow op='reorder_fields' in the schema_changelog check constraint.

The field-builder drag-to-reorder route (``PUT /crm/modules/{m}/fields/
order``) writes a ``schema_changelog`` audit row with
``op='reorder_fields'``. The original ``schema_changelog_op_check``
constraint only permitted add_field / update_field / hide_field /
add_option / hide_option, so every reorder failed with a
CheckViolation (HTTP 500). Extend the constraint to include
``reorder_fields``.

Revision ID: 0067
Revises: 0066
Create Date: 2026-05-21
"""
from alembic import op


revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

_OPS_WITH_REORDER = (
    "'add_field', 'update_field', 'hide_field', "
    "'add_option', 'hide_option', 'reorder_fields'"
)
_OPS_ORIGINAL = (
    "'add_field', 'update_field', 'hide_field', "
    "'add_option', 'hide_option'"
)


def _set_op_constraint(ops: str) -> None:
    op.execute(
        "ALTER TABLE schema_changelog "
        "DROP CONSTRAINT IF EXISTS schema_changelog_op_check"
    )
    op.execute(
        "ALTER TABLE schema_changelog "
        "ADD CONSTRAINT schema_changelog_op_check "
        f"CHECK (op = ANY (ARRAY[{ops}]::text[]))"
    )


def upgrade() -> None:
    _set_op_constraint(_OPS_WITH_REORDER)


def downgrade() -> None:
    _set_op_constraint(_OPS_ORIGINAL)
