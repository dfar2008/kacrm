"""Field writeback rules — kacrm port of ``field_writeback`` table.

Enables aggregate writeback from child records to parent records
(e.g. ``salesorders.total_amount`` = SUM(``salesorder_lines.amount``)).

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-17
"""
from alembic import op

revision = "0014"
down_revision = "0013"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE field_writeback (
            id BIGSERIAL PRIMARY KEY,
            source_module_id BIGINT NOT NULL,
            source_field_id BIGINT NOT NULL,
            target_module_id BIGINT NOT NULL,
            target_field_id BIGINT NOT NULL,
            calc_method TEXT NOT NULL DEFAULT 'original',
              -- 'original' | 'sum' | 'avg' | 'count' | 'max' | 'min'
            created_by BIGINT,
            updated_by BIGINT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (source_module_id, source_field_id,
                    target_module_id, target_field_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_field_writeback_source
        ON field_writeback (source_module_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS field_writeback")
