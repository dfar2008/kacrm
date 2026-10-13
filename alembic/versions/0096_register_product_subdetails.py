"""把内置产品子表 quotes_products / salesorders_products 注册为 product 子明细。

spec 2026-06-14 §3.2:这两张产品行表早于通用子明细系统存在(读写走 has_products
硬编码 + LineItemsEditor),但从未登记进 collections,所以 schema 页的子明细管理
看不到它们(用户反馈「销售订单/报价单无法设置子明细」)。

本迁移:
  1. 给两表补 ``deleted`` 列(与子明细系统/ dashboard 视图的 deleted 过滤一致;
     产品行读写是 DELETE-then-INSERT 硬删,不依赖它,纯为一致性 + 让 dashboard
     自动合成能建 v_dashboard_<t> 视图)。
  2. 在 collections 登记为父模块的 ``product`` 子明细。
  3. 灌关键 module_fields 元数据,让字段 builder 能展示产品行字段。

不改产品行读写路径:has_products 仍按 module_name 硬编码,LineItemsEditor 不读
元数据,故登记是纯增量、对现有报价/订单零行为变化。

Revision ID: 0096
Revises: 0095
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op


revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


_PRODUCT_SUBS = [
    # (child_table, parent_module)
    ("quotes_products", "quotes"),
    ("salesorders_products", "salesorders"),
]

# 关键产品行字段元数据(field_name, label, type, readonly, sort)。
# subtotal/total 是服务端算出的,设只读。
_PRODUCT_FIELDS = [
    ("products_id",   "产品",   "relation", False, 0),
    ("products_name", "产品名称", "text",     True,  1),
    ("quantity",      "数量",   "number",   False, 2),
    ("price",         "单价",   "currency", False, 3),
    ("discount_rate", "折扣率", "number",   False, 4),
    ("tax_rate",      "税率",   "number",   False, 5),
    ("subtotal",      "小计",   "currency", True,  6),
    ("total",         "合计",   "currency", True,  7),
    ("remark",        "备注",   "textarea", False, 8),
]


def upgrade() -> None:
    for child, parent in _PRODUCT_SUBS:
        # 1. deleted 列(幂等)
        op.execute(
            f'ALTER TABLE "{child}" '
            f"ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE"
        )
        # 2. 登记 collections(product 子明细)。幂等:已存在则跳过。
        op.execute(
            f"""
            INSERT INTO collections (name, title, title_en, parent_module_id, detail_kind)
            SELECT '{child}', '产品明细', 'Product lines',
                   (SELECT id FROM collections WHERE name = '{parent}'), 'product'
            WHERE NOT EXISTS (SELECT 1 FROM collections WHERE name = '{child}')
              AND EXISTS (SELECT 1 FROM collections WHERE name = '{parent}')
            """
        )
        # 3. 灌字段元数据(幂等:ON CONFLICT (module_id, field_name) DO NOTHING)
        for fname, label, ftype, readonly, sort in _PRODUCT_FIELDS:
            op.execute(
                f"""
                INSERT INTO module_fields
                    (module_id, field_name, field_label, field_type,
                     is_required, is_system, is_readonly, is_searchable, sort_order)
                SELECT c.id, '{fname}', '{label}', '{ftype}',
                       FALSE, FALSE, {str(readonly).upper()}, FALSE, {sort}
                FROM collections c WHERE c.name = '{child}'
                ON CONFLICT (module_id, field_name) DO NOTHING
                """
            )


def downgrade() -> None:
    for child, _parent in _PRODUCT_SUBS:
        op.execute(
            "DELETE FROM module_fields WHERE module_id = "
            f"(SELECT id FROM collections WHERE name = '{child}')"
        )
        op.execute(f"DELETE FROM collections WHERE name = '{child}'")
        op.execute(f'ALTER TABLE "{child}" DROP COLUMN IF EXISTS deleted')
