"""Partial index for crm_share notification dedup query.

The POST /collaborators handler writes one ``interaction_log`` row
per share grant (channel='crm_share') with the target record info in
the ``content`` jsonb column. Before each insert it dedups within a
24h window using:

    WHERE user_id = %s AND channel = 'crm_share'
      AND content->>'entity_type' = %s
      AND (content->>'entity_id')::bigint = %s
      AND created_at > NOW() - INTERVAL '24 hours'

The existing ``idx_interaction_user_time`` covers user_id but the
``content`` jsonb extraction degrades to a filter scan as the table
grows. A partial index keyed on the same expressions (scoped to
``channel='crm_share'`` only) keeps the dedup lookup index-only.

Revision ID: 0090
Revises: 0089
Create Date: 2026-05-26
"""
from alembic import op


revision = "0090"
down_revision = "0089"


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_interaction_crm_share
            ON interaction_log (
                user_id,
                ((content->>'entity_type')),
                (((content->>'entity_id')::bigint))
            )
            WHERE channel = 'crm_share'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interaction_crm_share")
