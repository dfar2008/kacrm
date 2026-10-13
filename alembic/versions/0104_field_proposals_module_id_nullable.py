"""field_proposals.module_id nullable — 让 module_add 提案(模块尚不存在)可入队。

非管理员通过 agent 发起的建模块请求会落成一条 kind='module_add' 的提案,
此时目标模块还没创建,没有 collections.id 可填,故放宽 NOT NULL。
其余 kind(field_add/subdetail_add/module_rename/workflow_add/picklist_option_add)
仍带真实 module_id。
"""
from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE field_proposals ALTER COLUMN module_id DROP NOT NULL")


def downgrade() -> None:
    # 回滚前清掉 module_id 为空的(module_add)提案,否则 SET NOT NULL 失败。
    op.execute("DELETE FROM field_proposals WHERE module_id IS NULL")
    op.execute("ALTER TABLE field_proposals ALTER COLUMN module_id SET NOT NULL")
