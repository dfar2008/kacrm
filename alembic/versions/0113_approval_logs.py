"""approval_logs — append-only 审批操作流水(审计)。

``approval_steps`` 每步只存最后一次结果(退回重审会覆盖、同一步多审批人只记
实际操作人),看不到完整操作历史。本表对每次 提交/通过/拒绝/退回/改派/撤销
追加一行、永不覆盖,供详情页「操作历史」时间线与审计使用。

Revision ID: 0113
Revises: 0112
Create Date: 2026-06-27
"""
from alembic import op

revision = "0113"
down_revision = "0112"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE approval_logs (
            id BIGSERIAL PRIMARY KEY,
            approval_id BIGINT NOT NULL,
            module_id BIGINT,
            data_id BIGINT,
            action TEXT NOT NULL,        -- submit|approve|reject|back|transfer|cancel
            step_order INTEGER,          -- 关联步序(整链动作为 NULL)
            step_name TEXT,
            actor_id BIGINT,             -- 操作人
            target_id BIGINT,            -- 改派目标(transfer)
            remark TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_approval_logs_approval
        ON approval_logs (approval_id, created_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approval_logs")
