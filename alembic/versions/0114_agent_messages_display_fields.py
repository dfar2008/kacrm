"""agent_messages 补 api_content / display_kind / display_metadata —— 对齐上游 v0.20。

升级遗留(同 0110 的模式):上游 v0.20 SessionState 重构后,run_agent 的
turn-boundary 批量落库(append_messages_batch)每行都带这三个字段;云端
PgSessionDB 之前把它们 strip/用 **kwargs 吞掉、表也没列。后果:

① display_kind 丢失 → 压缩摘要 carrier 行(display_kind='hidden',上游 #80622)
   重载后重新占据 active user slot、并在界面上显示成一条真实用户消息;
   retry/undo/会话分发全部按错位的用户回合计数。
② display_metadata 丢失 → 时间线簿记行(model_switch / auto_continue 等)的
   展示元数据、以及桌面端 reactions 的落点全部无处存放。
③ api_content 丢失 → "实际发送字节" sidecar 断链,重载会话后回放内容与
   provider prompt cache 前缀发散,缓存命中率退化(sent-bytes divergence
   capture,早于 v0.20 但同一缺口)。

三列全部可空、无默认,存量行为不变。display_metadata 用 JSONB(sqlite 端为
TEXT 存 JSON;PG 端 jsonb 读出即 dict,与 sqlite _decode_display_metadata
的产物一致)。
"""
from alembic import op

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS api_content TEXT"
    )
    op.execute(
        "ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS display_kind TEXT"
    )
    op.execute(
        "ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS display_metadata JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS display_metadata")
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS display_kind")
    op.execute("ALTER TABLE agent_messages DROP COLUMN IF EXISTS api_content")
