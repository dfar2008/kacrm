"""Extend system_options lock to salesorders.approval_status.

Why
---
0074 locked the scanner-referenced terminal values on 5 workflow fields
(receivables, leads.convertedstatus, quotes.status, salesorders.status,
tasks.status). ``salesorders.approval_status`` was missed — but it's
the same kind of business-critical state:

* The approval engine (``hermes/crm/approval/engine.py``) drives a
  separate ``approvals`` workflow table whose ``status`` lifecycle
  (``pending`` → ``passed`` / ``rejected``) is then mirrored back onto
  the salesorders column via approval hooks.
* ``hermes/crm/item_delete.py`` reads the approval state to decide
  whether deletion is allowed — admin-deleting an option here breaks
  that guard silently.

Per the user's call ("审批状态也应该是 system 字段") we lock all 3
seed values, matching the receivables pattern (whole option list as
system rather than just the terminal subset). No constant in
``hermes/crm/constants.py`` is added here because the approval engine
already owns the lifecycle vocabulary internally — the seed values
just need to stay present and stable.

Revision ID: 0076
Revises: 0075
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE module_fields
           SET field_config = COALESCE(field_config, '{}'::json)::jsonb
                              || jsonb_build_object(
                                     'system_options',
                                     jsonb_build_array('pending', 'approved', 'rejected')
                                 )
         WHERE field_name = 'approval_status'
           AND module_id IN (SELECT id FROM collections WHERE name = 'salesorders')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE module_fields
           SET field_config = (field_config::jsonb - 'system_options')::json
         WHERE field_name = 'approval_status'
           AND module_id IN (SELECT id FROM collections WHERE name = 'salesorders')
        """
    )
