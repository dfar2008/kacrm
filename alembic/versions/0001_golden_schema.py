"""Golden schema v0 — unified identity + RBAC + hermes primitives.

Scope (Phase 0 — just enough to stand up the unified backbone):

Identity & RBAC (adapted from kacrm, PostgreSQL-native):
  - users               integer PK, kacrm fields + api_key / plan
  - departments         org tree
  - roles               named role
  - permissions         verb registry (index/create/update/…)
  - role_permissions    (role_id, module_id, permission_id) -> has_permission
  - collections         metadata registry (kacrm)

Hermes primitives:
  - agents              per-user Agent instance (was ``profiles``)
  - interaction_log     append-only log of all channel traffic

Everything kacrm-specific beyond identity/RBAC (accounts, contacts, leads,
collection_fields, module_views, …) lands in a later revision.

Revision ID: 0001
Revises:
Create Date: 2026-04-15
"""
from __future__ import annotations

from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Identity & org
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE departments (
            id              BIGSERIAL PRIMARY KEY,
            name            VARCHAR(100) NOT NULL,
            parent_id       BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            manager_ids     JSONB DEFAULT '[]'::jsonb,
            sort_order      INTEGER DEFAULT 0,
            description     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_departments_parent_id ON departments(parent_id);")

    op.execute(
        """
        CREATE TABLE roles (
            id              BIGSERIAL PRIMARY KEY,
            name            VARCHAR(50) NOT NULL,
            is_default      BOOLEAN NOT NULL DEFAULT FALSE,
            description     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE permissions (
            id              BIGSERIAL PRIMARY KEY,
            name            VARCHAR(50) NOT NULL,
            code            VARCHAR(50) NOT NULL UNIQUE,
            description     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE collections (
            id              BIGSERIAL PRIMARY KEY,
            name            VARCHAR(255) NOT NULL UNIQUE,
            title           VARCHAR(255) NOT NULL
        );
        """
    )

    # role_permissions is the per-module permission grant matrix.
    # kacrm's original had no PK but we add a composite one — (role_id, module_id,
    # permission_id) is the semantic key.
    op.execute(
        """
        CREATE TABLE role_permissions (
            role_id         BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            permission_id   BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            module_id       BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            has_permission  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (role_id, module_id, permission_id)
        );
        """
    )
    op.execute("CREATE INDEX idx_role_permissions_role ON role_permissions(role_id);")

    # ------------------------------------------------------------------
    # users — unified identity (kacrm core fields + hermes api_key / plan)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE users (
            id              BIGSERIAL PRIMARY KEY,
            username        VARCHAR(50) NOT NULL UNIQUE,
            password        VARCHAR(255) NOT NULL,
            realname        VARCHAR(100),
            email           VARCHAR(320) UNIQUE,
            mobile          VARCHAR(100),
            status          SMALLINT NOT NULL DEFAULT 1,
            deleted         BOOLEAN NOT NULL DEFAULT FALSE,
            is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
            avatar          BIGINT,
            description     TEXT,
            department_id   BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            role_id         BIGINT REFERENCES roles(id) ON DELETE SET NULL,
            report_to_ids   JSONB DEFAULT '[]'::jsonb,
            api_key         VARCHAR(64) UNIQUE,
            plan            VARCHAR(32) NOT NULL DEFAULT 'free',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_users_dept ON users(department_id);")
    op.execute("CREATE INDEX idx_users_role ON users(role_id);")
    op.execute(
        "CREATE INDEX idx_users_active ON users(id) WHERE deleted = FALSE AND status = 1;"
    )

    # ------------------------------------------------------------------
    # Hermes primitives
    # ------------------------------------------------------------------
    # agents — one user may own many agents. The user's default agent has
    # is_default=TRUE; (user_id, slug) is unique for active rows.
    op.execute(
        """
        CREATE TABLE agents (
            id              BIGSERIAL PRIMARY KEY,
            user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name            VARCHAR(64) NOT NULL,
            slug            VARCHAR(64) NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            is_default      BOOLEAN NOT NULL DEFAULT FALSE,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            hermes_home     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_agents_user ON agents(user_id) WHERE is_active = TRUE;")
    op.execute(
        "CREATE UNIQUE INDEX uq_agents_user_slug_active "
        "ON agents(user_id, slug) WHERE is_active = TRUE;"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_agents_user_default "
        "ON agents(user_id) WHERE is_default = TRUE AND is_active = TRUE;"
    )

    # interaction_log — the append-only substrate. Every human/agent exchange
    # lands here. This is the source of truth for memory + RL training data.
    op.execute(
        """
        CREATE TABLE interaction_log (
            id              BIGSERIAL PRIMARY KEY,
            user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            agent_id        BIGINT REFERENCES agents(id) ON DELETE SET NULL,
            session_id      VARCHAR(64),
            channel         VARCHAR(32) NOT NULL,
            role            VARCHAR(16) NOT NULL,
            content         JSONB NOT NULL DEFAULT '{}'::jsonb,
            meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_interaction_user_time ON interaction_log(user_id, created_at DESC);")
    op.execute("CREATE INDEX idx_interaction_agent_time ON interaction_log(agent_id, created_at DESC);")
    op.execute("CREATE INDEX idx_interaction_session ON interaction_log(session_id) WHERE session_id IS NOT NULL;")

    # ------------------------------------------------------------------
    # updated_at trigger — kacrm relied on MySQL's ``ON UPDATE
    # CURRENT_TIMESTAMP``; PostgreSQL needs a trigger.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for tbl in ("users", "departments", "roles", "permissions", "agents"):
        op.execute(
            f"CREATE TRIGGER trg_{tbl}_updated_at BEFORE UPDATE ON {tbl} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS interaction_log;")
    op.execute("DROP TABLE IF EXISTS agents;")
    op.execute("DROP TABLE IF EXISTS users;")
    op.execute("DROP TABLE IF EXISTS role_permissions;")
    op.execute("DROP TABLE IF EXISTS collections;")
    op.execute("DROP TABLE IF EXISTS permissions;")
    op.execute("DROP TABLE IF EXISTS roles;")
    op.execute("DROP TABLE IF EXISTS departments;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
