"""users.decisions_rule_thresholds — per-user threshold overrides for
contextual prompt suggestions on the decisions surface.

Charter v2 §一系统: settings live server-side, NOT in localStorage.
Each manager can tune the 5 contextual rules (sleeping / stuck /
load_diff / accept_rate / revenue_drop) independently; the override
syncs across devices and survives browser cache clears.

Empty ``{}`` means "use defaults for every rule" — see
``RULE_THRESHOLDS_DEFAULT`` in
``hermes_cloud/app/decisions/prompt_suggestions.py``.

Revision ID: 0062
Revises: 0061
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "decisions_rule_thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "decisions_rule_thresholds")
