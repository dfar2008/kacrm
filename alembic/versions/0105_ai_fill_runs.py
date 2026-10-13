"""ai_fill_runs —— AI 字段填值去重/审计表。

同 (module, record, field, trigger) 短窗只跑一次,防 on_change 抖动重复跑。

Revision ID: 0105
Revises: 0104
Create Date: 2026-06-21
"""
from __future__ import annotations

from alembic import op


revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_fill_runs (
            id          BIGSERIAL PRIMARY KEY,
            module      VARCHAR(255) NOT NULL,
            record_id   BIGINT NOT NULL,
            field_name  VARCHAR(100) NOT NULL,
            trigger     VARCHAR(32) NOT NULL,
            status      VARCHAR(16) NOT NULL DEFAULT 'done',
            detail      JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_fill_runs_dedup "
        "ON ai_fill_runs (module, record_id, field_name, trigger, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_fill_runs")
