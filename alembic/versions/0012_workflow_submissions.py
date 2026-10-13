"""Workflow submissions — records Agent-triggered workflow actions.

Plan §8: the workflow.submit executor stores submissions here.
Supports approval chains, field updates, and notifications.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-16
"""
from alembic import op

revision = "0012"
down_revision = "0011"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE workflow_submissions (
            id BIGSERIAL PRIMARY KEY,
            workflow_type TEXT NOT NULL,
            module_name TEXT NOT NULL,
            record_id BIGINT NOT NULL,
            submitted_by BIGINT,
            agent_source TEXT,
            payload JSONB,
            status TEXT NOT NULL DEFAULT 'pending',
            processed_at TIMESTAMPTZ,
            result JSONB,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_workflow_submissions_status
        ON workflow_submissions (status)
        WHERE status = 'pending'
    """)
    op.execute("""
        CREATE INDEX idx_workflow_submissions_record
        ON workflow_submissions (module_name, record_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_submissions")
