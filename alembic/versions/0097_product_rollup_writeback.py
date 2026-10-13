"""注册产品行的标准汇总规则(合计→最终金额、数量→总数量),让汇总规则面板可见。

quotes/salesorders 的最终金额/总数量原本只由 _save_line_items 内置硬编码 rollup,
汇总规则面板里看不到任何规则(用户反馈「产品明细没有汇总规则」)。本迁移把这两条
标准 rollup 注册成 field_writeback 规则:
  - <child>.total    SUM → <parent>.final_amount
  - <child>.quantity SUM → <parent>.total_quantity

配合 save.py 让 _save_line_items 也触发 writeback,这些规则会真正生效(标准规则
幂等重算同值,与内置 rollup 一致;用户在面板新增的规则也照常滚动)。目标字段
(final_amount/total_quantity)标只读(系统计算)。

Revision ID: 0097
Revises: 0096
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op


revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


# (child_table, parent_module, source_field, target_field, calc)
_RULES = [
    ("salesorders_products", "salesorders", "total",    "final_amount",   "sum"),
    ("salesorders_products", "salesorders", "quantity", "total_quantity", "sum"),
    ("quotes_products",      "quotes",      "total",    "final_amount",   "sum"),
    ("quotes_products",      "quotes",      "quantity", "total_quantity", "sum"),
]


def upgrade() -> None:
    for child, parent, sfield, tfield, calc in _RULES:
        # 注册规则(幂等:同 source/target 已存在则跳过)
        op.execute(
            f"""
            INSERT INTO field_writeback
                (source_module_id, source_field_id, target_module_id,
                 target_field_id, calc_method, created_by, updated_by)
            SELECT sm.id, sf.id, tm.id, tf.id, '{calc}', 1, 1
            FROM collections sm
            JOIN collections tm ON tm.name = '{parent}'
            JOIN module_fields sf ON sf.module_id = sm.id AND sf.field_name = '{sfield}'
            JOIN module_fields tf ON tf.module_id = tm.id AND tf.field_name = '{tfield}'
            WHERE sm.name = '{child}'
              AND NOT EXISTS (
                SELECT 1 FROM field_writeback w
                WHERE w.source_module_id = sm.id AND w.source_field_id = sf.id
                  AND w.target_module_id = tm.id AND w.target_field_id = tf.id)
            """
        )
        # 目标字段标只读(系统计算,SPA 手填表单禁编辑)
        op.execute(
            f"""
            UPDATE module_fields SET is_readonly = TRUE
            WHERE module_id = (SELECT id FROM collections WHERE name = '{parent}')
              AND field_name = '{tfield}'
            """
        )


def downgrade() -> None:
    for child, parent, sfield, tfield, _calc in _RULES:
        op.execute(
            f"""
            DELETE FROM field_writeback w
            USING collections sm, collections tm,
                  module_fields sf, module_fields tf
            WHERE sm.name = '{child}' AND tm.name = '{parent}'
              AND sf.module_id = sm.id AND sf.field_name = '{sfield}'
              AND tf.module_id = tm.id AND tf.field_name = '{tfield}'
              AND w.source_module_id = sm.id AND w.source_field_id = sf.id
              AND w.target_module_id = tm.id AND w.target_field_id = tf.id
            """
        )
