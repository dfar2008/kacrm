"""归一 legacy 选项类字段:select/radio→picklist,multipicklist→multiselect。

`select` 是 `picklist` 的旧名,二者底层完全相同(同 SQL 列类型、同选项存储),
只因历史命名分叉成两个 field_type,导致到处特判、且 AI 自动填充白名单漏掉
legacy 值。本迁移把 module_fields 的 field_type 元数据标签归一到 canonical,
**只改标签,不动实际列、不动任何记录值**。

Revision ID: 0107
Revises: 0106
Create Date: 2026-06-23
"""
from __future__ import annotations

from alembic import op


revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 单选别名 → picklist
    op.execute(
        "UPDATE module_fields SET field_type = 'picklist' "
        "WHERE field_type IN ('select', 'radio')"
    )
    # 多选别名 → multiselect
    op.execute(
        "UPDATE module_fields SET field_type = 'multiselect' "
        "WHERE field_type = 'multipicklist'"
    )


def downgrade() -> None:
    # 归一不可逆:原 select/radio/multipicklist 的区分信息已无意义且未保留,
    # 且 picklist/multiselect 与之功能等价,故 downgrade 为 no-op。
    pass
