"""channel_bindings —— 渠道 external_id 到 CRM 记录的映射表。

将飞书/企微等渠道的 open_id 等 external_id 映射到 CRM 模块记录，
用于 inbound 消息的联系人解析（P0b）。

Revision ID: 0106
Revises: 0105
Create Date: 2026-06-23
"""
from __future__ import annotations

from alembic import op


revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_bindings (
            id          BIGSERIAL PRIMARY KEY,
            channel     TEXT NOT NULL,
            external_id TEXT NOT NULL,
            module      TEXT NOT NULL,
            record_id   BIGINT NOT NULL,
            bound_by    BIGINT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_channel_bindings_channel_external
                UNIQUE (channel, external_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_channel_bindings_module_record "
        "ON channel_bindings (module, record_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS channel_bindings")
