"""Drop the built-in Accounting and Support roles.

Neither role is used in this deployment — Accounting had a single
soft-deleted user (``linzong / 林总``, ``deleted=TRUE``) and Support
had zero users. The org structure consolidated around General Manager
/ Sales Manager / Sales, so we're collapsing the role catalog to
match reality rather than carrying dead seed defaults forever.

Order matters:

1. Hard-delete any soft-deleted users still pinned to these roles.
   ``users.role_id → roles.id`` is ``ON DELETE SET NULL``, so a live
   user would survive role removal as a "no-role" account — fine for
   active users, wrong for soft-deleted ghosts we want gone entirely.
   The 49 FKs pointing at ``users.id`` are split CASCADE / SET NULL,
   so this delete cleanly nulls historical attribution
   (created_by / owner_by / approval_user_id …) without losing the
   business records they wrote.
2. Drop the roles. ``role_permissions.role_id`` cascades, so the 53
   ``role_permissions`` rows for these two roles disappear with them.

Note for future seed work: ``hermes/crm/seed.py``'s ``DEFAULT_ROLES``
and ``DEFAULT_ROLE_GRANTS`` still list Accounting & Support. The
current ``scripts/seed_initial_data.py`` is broken (imports a
nonexistent ``hermes_cloud.app.crm.seed``) so it doesn't fire in
practice; if seed is ever revived, prune those entries via the
hermes-patches workflow.

Revision ID: 0088
Revises: 0087
Create Date: 2026-05-26
"""
from alembic import op


revision = "0088"
down_revision = "0087"


_ROLES = ("Accounting", "Support")


def upgrade() -> None:
    # Step 1: purge soft-deleted users still attached to these roles.
    # `deleted = TRUE` is the gate — we don't touch active users (there
    # are none in practice; this stays defensive for re-runs).
    op.execute(f"""
        DELETE FROM users
         WHERE deleted = TRUE
           AND role_id IN (
             SELECT id FROM roles WHERE name IN {_ROLES}
           )
    """)
    # Step 2: drop the roles. role_permissions cascades; any active
    # users (none expected) get role_id → NULL via the existing FK.
    op.execute(f"DELETE FROM roles WHERE name IN {_ROLES}")


def downgrade() -> None:
    # Symmetric with 0085/0086/0087: no-op. Reviving these roles would
    # need their full role_permissions matrix from the kacrm seed, and
    # the purged ghost users are unrecoverable anyway.
    pass
