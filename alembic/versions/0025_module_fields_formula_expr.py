"""module_fields.formula_expr — completes kacrm formula-field port.

The formula post-save hook in ``hermes_cloud/app/crm/save.py``
(``_formula_fields_after_save``) reads ``module_fields.formula_expr`` to
re-evaluate ``field_type='formula'`` columns. The original ``0003``
migration never added this column, so the SELECT errored and poisoned
the implicit transaction (see ``bb9aff0`` for the defensive rollback
that unblocked the gateway approve flow).

This migration adds the missing column. It's nullable and has no
default — existing rows remain untouched, and the formula hook simply
continues to skip any field that has no expression.

Revision ID: 0025
Revises: 0024
"""

from alembic import op


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE module_fields "
        "ADD COLUMN IF NOT EXISTS formula_expr TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE module_fields DROP COLUMN IF EXISTS formula_expr")
