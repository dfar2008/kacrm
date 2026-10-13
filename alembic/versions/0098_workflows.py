"""工作流引擎:workflows(规则)+ workflow_runs(执行日志/审计/防重)。

北极星:用户口述自动化规则,agent 当场把它建成可执行的工作流。
- 规则与执行都由 agent 著作;每个动作选执行器:确定性动作(改字段/建任务/
  建记录)由引擎直接执行(可靠、零 LLM、可重放),判断/沟通动作丢给 per-profile
  agent 跑一轮(run_utility_skill)。
- 触发:event(写记录命中)经 emit_business_write 后置入 celery;schedule 经
  celery beat tick。
- workflow_runs 沿用事件溯源:每次执行落一行,既可审计也可防重复执行。

Revision ID: 0098
Revises: 0097
Create Date: 2026-06-15
"""
from __future__ import annotations

from alembic import op


revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            id            BIGSERIAL PRIMARY KEY,
            name          TEXT NOT NULL,
            enabled       BOOLEAN NOT NULL DEFAULT TRUE,
            trigger_type  TEXT NOT NULL
                          CHECK (trigger_type IN ('event', 'schedule')),
            module        TEXT NOT NULL,
            trigger_on    TEXT
                          CHECK (trigger_on IN ('create', 'update') OR trigger_on IS NULL),
            condition     JSONB NOT NULL DEFAULT '{}'::jsonb,
            schedule_cron TEXT,
            actions       JSONB NOT NULL DEFAULT '[]'::jsonb,
            last_run_at   TIMESTAMP WITH TIME ZONE,
            created_by    INTEGER,
            updated_by    INTEGER,
            created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            deleted       BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    # 事件触发的热查询:按 (module, trigger_on) 找启用的 event 工作流。
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_workflows_event_lookup
            ON workflows (module, trigger_on)
         WHERE trigger_type = 'event' AND enabled AND NOT deleted
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_workflows_schedule
            ON workflows (trigger_type)
         WHERE trigger_type = 'schedule' AND enabled AND NOT deleted
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id                 BIGSERIAL PRIMARY KEY,
            workflow_id        BIGINT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            trigger_record_id  BIGINT,
            trigger            TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'running'
                               CHECK (status IN ('running', 'done', 'failed', 'skipped')),
            detail             JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_runs_workflow "
        "ON workflow_runs (workflow_id, created_at DESC)"
    )
    # 防重复执行:同一工作流 × 同一触发记录 × 同一触发类型,成功执行只记一次。
    # (event 触发可能因 at-least-once 投递重放;run_workflow 先查这个唯一键。)
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_runs_dedupe
            ON workflow_runs (workflow_id, trigger_record_id, trigger)
         WHERE status = 'done' AND trigger_record_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_runs")
    op.execute("DROP TABLE IF EXISTS workflows")
