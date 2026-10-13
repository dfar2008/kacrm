"""Add module_fields.field_config — type-specific field configuration.

A single nullable JSON column on ``module_fields`` that holds all
per-field-type configuration introduced by the schema field builder:

    {
      "label_i18n": { "<locale>": "<label override>" },
      "autonumber": { "prefix": "SO-", "digits": 5,
                      "start": 1, "date_token": "YYYYMM" },
      "formula":    { "expression": "quantity * price" },
      "relation":   { "target_module": "accounts" }
    }

All keys are optional. Legacy rows stay NULL — every reader uses
``?.``-style optional access, so no backfill is required.

We picked one JSON column over four typed columns: formula and
autonumber each need their own config blob, and a column per concern
would balloon the table. One JSON column = one migration covers all.

Revision ID: 0066
Revises: 0065
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa


revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "module_fields",
        sa.Column("field_config", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("module_fields", "field_config")
