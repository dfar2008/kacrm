"""工作流 draft/live 生命周期态(S4)。

给 ``workflows`` 加 ``status`` 列(draft/live),默认 ``live`` 让存量工作流行为不变。
仅 ``status='live'`` 才被 event/schedule 触发;``draft`` 只能经 test-run 试跑。

Revision ID: 0108
Revises: 0107
Create Date: 2026-06-24
"""
from __future__ import annotations

from alembic import op


revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 默认 'live' —— 存量工作流照常被触发;CHECK 兜住非法值。
    # 用 raw SQL + IF NOT EXISTS,匹配本仓迁移风格(可重入)。
    op.execute(
        "ALTER TABLE workflows "
        "ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'live' "
        "CHECK (status IN ('draft', 'live'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workflows DROP COLUMN IF EXISTS status")
