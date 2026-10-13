"""agent_messages 加 step-timing 列:duration_ms(每行该步耗时)+ turn_meta(回合末行汇总)。

duration_ms: assistant 行=该段生成(API)耗时;tool 行=工具执行耗时;user 行=NULL。
turn_meta:  仅回合最后一条 assistant 行非空,载 {total_ms, queued_ms, api_calls, tool_count}。
幂等:IF NOT EXISTS。旧行两列为 NULL,前端不显示徽章(优雅降级)。
"""
from alembic import op

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS duration_ms INTEGER")
    op.execute("ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS turn_meta JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS turn_meta")
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS duration_ms")
