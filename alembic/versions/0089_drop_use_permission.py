"""Drop the dead 'use' permission column.

Companion to 0085-0088. The ``USE`` column in the role matrix was
originally added for skill / hermes modules — both kinds got torn
down in 0085 / 0086. No remaining route or code path calls
``check_permission(..., 'use')``. We're removing the permission
row entirely so the matrix UI drops the column automatically.

Revision ID: 0089
Revises: 0088
Create Date: 2026-05-26
"""
from alembic import op


revision = "0089"
down_revision = "0088"


def upgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id = (SELECT id FROM permissions WHERE code = 'use')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'use'")


def downgrade() -> None:
    # Same stance as 0085-0088: no-op. Reinstating 'use' would require
    # repopulating role grants that no live code consumes anyway.
    pass
