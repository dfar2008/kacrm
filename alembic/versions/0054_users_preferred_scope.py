"""users.preferred_scope — Phase 5.5 (plan v1.2 §5)

Per-user default scope for SPA list / dashboard segments. ``NULL``
means "use the default for the user's role":

  · non-manager NULL → SPA defaults to 'self'
  · manager NULL     → SPA defaults to 'team'
  · admin NULL       → SPA defaults to 'team' (NOT 'org' — org is
                        opt-in per access; admin still sees their
                        team by default to match the §定律 4 push
                        cadence)

Revision ID: 0054
Revises: 0053
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa


revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "preferred_scope", sa.Text(), nullable=True,
        ),
    )
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_preferred_scope_chk "
        "CHECK (preferred_scope IS NULL "
        "       OR preferred_scope IN ('self','team','org'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS users_preferred_scope_chk"
    )
    op.drop_column("users", "preferred_scope")
