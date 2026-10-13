"""Per-role / per-user profile creation quota.

Adds the storage columns that back the quota resolver used by
``POST /api/profiles``. Quota resolution order is::

    is_admin → unlimited
    users.profile_quota (per-user override)
    roles.profile_quota (role default)
    hard-coded fallback → 1

Both columns allow NULL (= delegate to the next layer). Any stored
value must be ``>= 1`` — a quota of 0 makes no sense because new users
are auto-provisioned with one default profile on creation, and the
default profile cannot be deleted/disabled.

Schema changes
--------------
* ``roles`` gains ``profile_quota INTEGER NULL`` + CHECK (>=1 OR NULL).
* ``users`` gains ``profile_quota INTEGER NULL`` + same CHECK.

Data changes
------------
* Built-in role defaults:
  - ``Sales Manager``                       → 5
  - ``Sales`` / ``Support`` / ``Accounting``→ 1
  - ``General Manager``                     → NULL (unlimited; also
    is_admin short-circuits at the resolver)

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-17
"""
from alembic import op


revision = "0017"
down_revision = "0016"


ROLE_QUOTA_DEFAULTS = [
    ("Sales Manager",   5),
    ("Sales",           1),
    ("Support",         1),
    ("Accounting",      1),
    # "General Manager": NULL (unlimited) — left at default
]


def upgrade() -> None:
    # roles.profile_quota
    op.execute(
        "ALTER TABLE roles ADD COLUMN IF NOT EXISTS profile_quota INTEGER NULL"
    )
    op.execute(
        "ALTER TABLE roles DROP CONSTRAINT IF EXISTS ck_roles_profile_quota_min"
    )
    op.execute(
        "ALTER TABLE roles ADD CONSTRAINT ck_roles_profile_quota_min "
        "CHECK (profile_quota IS NULL OR profile_quota >= 1)"
    )

    # users.profile_quota
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_quota INTEGER NULL"
    )
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_profile_quota_min"
    )
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_profile_quota_min "
        "CHECK (profile_quota IS NULL OR profile_quota >= 1)"
    )

    # Seed built-in role defaults. Only touch rows still NULL so an admin
    # tweak made after deploy is never clobbered.
    for role_name, cap in ROLE_QUOTA_DEFAULTS:
        op.execute(
            f"UPDATE roles SET profile_quota = {int(cap)} "
            f"WHERE name = '{role_name}' AND profile_quota IS NULL"
        )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_profile_quota_min"
    )
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS profile_quota")
    op.execute(
        "ALTER TABLE roles DROP CONSTRAINT IF EXISTS ck_roles_profile_quota_min"
    )
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS profile_quota")
