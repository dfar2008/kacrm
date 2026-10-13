"""agent_messages 补 platform_message_id / observed —— 对齐上游 v0.17 append_message。

升级遗留:上游 v2026.6.19 的 append_message 新增 platform_message_id / observed /
timestamp;网关在传前两个,但云端 PgSessionDB 把它们用 **kwargs 吞了、表也没列。
后果:① observed(群聊旁观消息标志)丢失 → 旁观上下文混进可回放历史(上游用它
withhold,缺了会让 assistant→tool 序列错乱、有 API 500 风险);② platform_message_id
丢失 → 平台消息引用/查重索引在云端为空。

加两列 + 上游同款非唯一查重索引(WHERE platform_message_id IS NOT NULL)。
"""
from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS platform_message_id TEXT"
    )
    # observed: INTEGER 0/1 (mirror upstream sqlite ``observed INTEGER DEFAULT 0``).
    op.execute(
        "ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS observed INTEGER DEFAULT 0"
    )
    # 上游同款查重/查找索引(非唯一,只为按 platform_message_id 快查)。
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_messages_platform_msg_id
            ON agent_messages (session_id, platform_message_id)
         WHERE platform_message_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_messages_platform_msg_id")
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS observed")
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS platform_message_id")
