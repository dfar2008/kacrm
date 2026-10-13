"""Workflow rules + approval chains — kacrm port.

Plan §8 extension: ports kacrm's ``workflow`` / ``modules_approval`` /
``approvals`` / ``approval_steps`` / ``workflowtimeup`` tables to PG
for the hermes-cloud workflow engine.

Source files in kacrm (reference):
- app/includes/workflow/workflow_edit.py
- app/includes/workflow/workflow_time_up.py
- app/models/approvals_db.py

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-17
"""
from alembic import op

revision = "0013"
down_revision = "0012"


def upgrade() -> None:
    # ----- Workflow rules (kacrm `workflow` table) ------------------------
    op.execute("""
        CREATE TABLE workflow_rules (
            workflowid BIGSERIAL PRIMARY KEY,
            workflow_type TEXT NOT NULL,          -- 'add' | 'edit' | 'timeup'
            trigger_module TEXT NOT NULL,         -- module name (accounts, leads, …)
            content JSONB NOT NULL,               -- {condition, action}
            status BOOLEAN NOT NULL DEFAULT TRUE,
            created_by BIGINT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_workflow_rules_active
        ON workflow_rules (workflow_type, trigger_module)
        WHERE status = TRUE
    """)

    # ----- Workflow timeup state (dedup daily runs) -----------------------
    op.execute("""
        CREATE TABLE workflow_timeup_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            execute_date DATE,
            CONSTRAINT workflow_timeup_state_singleton CHECK (id = 1)
        )
    """)

    # ----- Approval chain config per module (kacrm `modules_approval`) ----
    op.execute("""
        CREATE TABLE approval_configs (
            module_id BIGINT PRIMARY KEY,
            steps JSONB NOT NULL,                 -- [{step_name, users: [uid,…]}, …]
            allow_edit_after_approve SMALLINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # ----- Approval instances (kacrm `approvals`) -------------------------
    op.execute("""
        CREATE TABLE approvals (
            id BIGSERIAL PRIMARY KEY,
            module_id BIGINT NOT NULL,
            data_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', -- pending | passed | rejected | cancelled
            created_by BIGINT NOT NULL,
            current_step INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX idx_approvals_module_data
        ON approvals (module_id, data_id)
    """)
    op.execute("""
        CREATE INDEX idx_approvals_pending
        ON approvals (status)
        WHERE status = 'pending'
    """)

    # ----- Approval step instances (kacrm `approval_steps`) ---------------
    # approver_ids stored as comma-separated string to match kacrm semantics;
    # queries use string_to_array() for membership checks.
    op.execute("""
        CREATE TABLE approval_steps (
            id BIGSERIAL PRIMARY KEY,
            approval_id BIGINT NOT NULL REFERENCES approvals(id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL,
            step_name TEXT,
            approver_ids TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'waiting', -- pending | waiting | passed | rejected | backed
            remark TEXT,
            actual_approver_id BIGINT,
            approved_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX idx_approval_steps_approval
        ON approval_steps (approval_id, step_order)
    """)
    op.execute("""
        CREATE INDEX idx_approval_steps_pending
        ON approval_steps (status)
        WHERE status = 'pending'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approval_steps")
    op.execute("DROP TABLE IF EXISTS approvals")
    op.execute("DROP TABLE IF EXISTS approval_configs")
    op.execute("DROP TABLE IF EXISTS workflow_timeup_state")
    op.execute("DROP TABLE IF EXISTS workflow_rules")
