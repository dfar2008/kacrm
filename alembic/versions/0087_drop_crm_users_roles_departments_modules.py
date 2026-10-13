"""Drop dead CRM-side permission modules: users / roles / departments.

These were inherited from kacrm's original seed (collections rows that
treat users/roles/departments as record-style modules). Nothing in
``hermes_cloud/`` or ``hermes/crm/`` actually calls
``check_permission(uid, 'users'/'roles'/'departments', ...)`` — the
real admin pages gate on ``admin_users / admin_departments /
admin_roles`` instead (migration 0016 + ``_require_admin_module_view``
in main.py).

Companion to 0085 (skill kind) and 0086 (hermes kind). After this the
CRM tab in the role editor drops from 13 modules to 10.

Note for future seed work: ``hermes/crm/seed.py`` still lists these in
``DEFAULT_COLLECTIONS`` and ``DEFAULT_ROLE_GRANTS``. The current
``scripts/seed_initial_data.py`` is broken (imports a nonexistent
module) and doesn't run in practice, so it's not a live risk; if seed
is ever revived, drop those entries via the hermes-patches workflow.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-26
"""
from alembic import op


revision = "0087"
down_revision = "0086"


_DEAD_MODULES = ("users", "roles", "departments")


def upgrade() -> None:
    op.execute(f"""
        DELETE FROM role_permissions
         WHERE module_id IN (
             SELECT id FROM collections WHERE name IN {_DEAD_MODULES}
         )
    """)
    op.execute(f"DELETE FROM collections WHERE name IN {_DEAD_MODULES}")


def downgrade() -> None:
    # Symmetric with 0085/0086: no-op. The original kacrm seed could
    # repopulate the collection rows but not the role grants we just
    # wiped, so a partial revert would be worse than nothing.
    pass
