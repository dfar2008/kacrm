"""Tear down kind='hermes' permission modules.

Companion to 0085 (which tore down kind='skill'). The Hermes-native
modules registered by 0016 (agents/chat/mail/touchpoints/analytics/
approvals) are no longer surfaced in the role editor:

* ``agents``, ``chat``, ``mail``, ``touchpoints`` were never actually
  wired to a route gate (``require_module_permission`` is dead code).
* ``analytics`` and ``approvals`` are now admin-only — the SPA nav
  guards via ``hasGrant("…")`` which short-circuits on ``is_admin``.
  Once the collection rows are gone, no non-admin can possibly hold
  those grants, so the menu auto-hides.

Schema-wise: collections.kind / description / metadata columns and
the kind index all stay; CRM and Admin modules still use them.

Revision ID: 0086
Revises: 0085
Create Date: 2026-05-26
"""
from alembic import op


revision = "0086"
down_revision = "0085"


def upgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE module_id IN (
             SELECT id FROM collections WHERE kind = 'hermes'
         )
    """)
    op.execute("DELETE FROM collections WHERE kind = 'hermes'")


def downgrade() -> None:
    # Symmetric with 0085: the original rows came from 0016's seed list
    # — reverting would require duplicating that data. We don't.
    pass
