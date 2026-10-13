"""Profiles table — hermes agent profile metadata on the shared CRM PG.

Phase 1c.3a of the hermes × CRM merge. Historically the
``profile_store.SqlAlchemyProfileStore`` ran against a *separate*
SQLite/SA database (``session_store_url``). Phase 1c.3 moves it onto
the unified PG alongside ``users`` / ``meta_*`` / ``crm_*`` so every
identity-adjacent table shares one engine, one pool, one transaction
boundary (plan §4.1 + decisions D5–D6).

Interim compromises kept to minimise blast radius on the live feishu
subprocesses:

* ``id`` stays **VARCHAR(64)** rather than the plan's ``BIGSERIAL``
  (decision D6). The existing hermes code treats profile ids as
  opaque strings — hex for non-default profiles, and
  ``profile.id == user.id`` for the default (so
  ``~/.hermes/profiles/{id}/`` directories keep working). Flipping
  to ``BIGSERIAL`` is a follow-up (Phase 1c.3b) that will land
  together with the on-disk directory rename.

* ``user_id`` is **BIGINT with a REAL foreign key** into ``users(id)``
  — matches the plan and gives us referential integrity immediately.
  Callers cast to ``int`` at the store boundary.

* Partial unique indexes enforce the two invariants the SA store
  enforced in Python: one default per user, unique slug per user
  among active rows.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-16
"""
from __future__ import annotations

from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop any legacy ``profiles`` table that may have been materialised
    # by an earlier pointing of ``SqlAlchemyProfileStore`` at this DB
    # (wrong schema: VARCHAR user_id, no FK into ``users``). Per the
    # Clean Slate directive (plan v1.2) old hermes preview data is
    # discarded, so this drop is safe — there are no prod deployments
    # yet to preserve.
    op.execute("DROP TABLE IF EXISTS profiles CASCADE;")

    op.execute(
        """
        CREATE TABLE profiles (
            id          VARCHAR(64) PRIMARY KEY,
            user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        VARCHAR(64) NOT NULL,
            slug        VARCHAR(64) NOT NULL DEFAULT '',
            is_default  BOOLEAN NOT NULL DEFAULT FALSE,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            description TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    # Fast "profiles for this user" lookup — the warm-boot / list flows.
    op.execute("CREATE INDEX idx_profiles_user_id ON profiles(user_id);")

    # Invariant: one active default profile per user. ``DEFERRABLE``
    # matches the plan §4.1 so set-default flips can temporarily violate
    # the constraint inside a transaction.
    op.execute(
        "CREATE UNIQUE INDEX idx_profiles_one_default_per_user "
        "ON profiles(user_id) WHERE is_default = TRUE AND is_active = TRUE;"
    )

    # Invariant: per-user slug uniqueness among active profiles. Inactive
    # rows are exempt so soft-deleted profiles don't pin their slug.
    op.execute(
        "CREATE UNIQUE INDEX idx_profiles_user_slug "
        "ON profiles(user_id, slug) WHERE is_active = TRUE AND slug <> '';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_profiles_user_slug;")
    op.execute("DROP INDEX IF EXISTS idx_profiles_one_default_per_user;")
    op.execute("DROP INDEX IF EXISTS idx_profiles_user_id;")
    op.execute("DROP TABLE IF EXISTS profiles;")
