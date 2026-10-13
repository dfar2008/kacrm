"""collections 加模块树三列:parent_module_id / detail_kind / title_en。

支撑「自定义模块 + 子明细」(spec 2026-06-14-conversational-schema-evolution)。
- parent_module_id: NULL=顶层模块;非 NULL=某模块的子明细(P2b 用)
- detail_kind: 子明细类型 'product'|'generic'(P2b 用),顶层 NULL
- title_en: 英文 label(i18n);NULL 时前端回退 title

Revision ID: 0095
Revises: 0094
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op


revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE collections
            ADD COLUMN IF NOT EXISTS parent_module_id BIGINT
                REFERENCES collections(id) ON DELETE RESTRICT,
            ADD COLUMN IF NOT EXISTS detail_kind VARCHAR(20),
            ADD COLUMN IF NOT EXISTS title_en VARCHAR(255)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_collections_parent "
        "ON collections(parent_module_id) WHERE parent_module_id IS NOT NULL"
    )
    # schema_changelog 增加模块/子明细/汇总级 op(provision_module / rename /
    # P2b 子明细 + writeback)。原 CHECK 只允许字段级 op。
    op.execute(
        "ALTER TABLE schema_changelog DROP CONSTRAINT IF EXISTS "
        "schema_changelog_op_check"
    )
    op.execute(
        """
        ALTER TABLE schema_changelog ADD CONSTRAINT schema_changelog_op_check
        CHECK (op = ANY (ARRAY[
            'add_field','update_field','hide_field','add_option','hide_option',
            'reorder_fields','delete_option','rename_option_label','reorder_options',
            'add_module','rename_module','add_subdetail',
            'add_writeback','update_writeback','delete_writeback'
        ]))
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE schema_changelog DROP CONSTRAINT IF EXISTS "
        "schema_changelog_op_check"
    )
    op.execute(
        """
        ALTER TABLE schema_changelog ADD CONSTRAINT schema_changelog_op_check
        CHECK (op = ANY (ARRAY[
            'add_field','update_field','hide_field','add_option','hide_option',
            'reorder_fields','delete_option','rename_option_label','reorder_options'
        ]))
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_collections_parent")
    op.execute(
        "ALTER TABLE collections "
        "DROP COLUMN IF EXISTS parent_module_id, "
        "DROP COLUMN IF EXISTS detail_kind, "
        "DROP COLUMN IF EXISTS title_en"
    )
