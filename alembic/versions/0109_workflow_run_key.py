"""工作流去重改用触发实例键 run_key —— 让"每次更新/每个定时 tick"都能各跑一次。

根因:原去重键 (workflow_id, trigger_record_id, trigger),trigger 是常量
('event:update' / 'schedule'),把"重复投递"和"不同的触发实例"混为一谈 → 同记录
只跑一次。改成 run_key(event=每次写的 uuid;schedule=tick:record):重复投递同
run_key 仍挡住,不同更新/不同 tick 不同 run_key 放行。
"""
from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS run_key TEXT")
    # 旧去重索引(按 trigger)会把"每次更新"误判重复,删之。
    op.execute("DROP INDEX IF EXISTS uq_workflow_runs_dedupe")
    # 新去重:同 工作流 × 触发实例 的成功执行只记一次。
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_runs_runkey
            ON workflow_runs (workflow_id, run_key)
         WHERE status = 'done' AND run_key IS NOT NULL
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_runs_runkey "
        "ON workflow_runs (workflow_id, run_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_workflow_runs_runkey")
    op.execute("DROP INDEX IF EXISTS ix_workflow_runs_runkey")
    op.execute("ALTER TABLE workflow_runs DROP COLUMN IF EXISTS run_key")
