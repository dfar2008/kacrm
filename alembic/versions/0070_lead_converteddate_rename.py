"""Rename leads.convertedat → converteddate, rebuild v_dashboard_leads.

Revision ID: 0070
Revises: 0069
Create Date: 2026-05-22
"""
from __future__ import annotations

from alembic import op


revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_dashboard_leads CASCADE;")
    op.execute("ALTER TABLE leads RENAME COLUMN convertedat TO converteddate;")
    op.execute(
        """
        CREATE VIEW v_dashboard_leads AS
        SELECT
            leads_id        AS id,
            leads_name      AS name,
            owner_by        AS owner_user_id,
            department_id,
            status,
            source,
            convertedstatus,
            converteddate,
            contact,
            phone,
            mobile,
            email,
            created_at,
            updated_at
        FROM leads
        WHERE deleted = FALSE;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_dashboard_leads CASCADE;")
    op.execute("ALTER TABLE leads RENAME COLUMN converteddate TO convertedat;")
    op.execute(
        """
        CREATE VIEW v_dashboard_leads AS
        SELECT
            leads_id        AS id,
            leads_name      AS name,
            owner_by        AS owner_user_id,
            department_id,
            status,
            source,
            convertedstatus,
            convertedat,
            contact,
            phone,
            mobile,
            email,
            created_at,
            updated_at
        FROM leads
        WHERE deleted = FALSE;
        """
    )
