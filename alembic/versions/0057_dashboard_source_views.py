"""Dashboards source views — one per semantic-layer entity.

Each view normalizes column names so the YAML / DSL stays clean
(e.g. accounts.accounts_id -> v_dashboard_customers.id). Views also
enforce ``deleted = false`` so the semantic layer never sees soft-deleted
rows.

Helper: ``v_team_members(caller_id BIGINT)`` returns user IDs reachable
via the report_to_ids JSONB graph (caller themselves + direct reports).
This is the team scope's source of truth (hermes-cloud has no teams table).

Revision ID: 0057
Revises: 0056
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op


revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotency: dashboards' runtime_schema may have already created
    # these views (with possibly different column shape) on a prior app
    # boot. CREATE OR REPLACE VIEW cannot alter column count/order, so
    # drop first then recreate. CASCADE handles any dependent objects
    # (none expected — these are dashboard-internal).
    for v in (
        "v_dashboard_memories",
        "v_dashboard_tasks",
        "v_dashboard_ai_actions",
        "v_dashboard_departments",
        "v_dashboard_users",
        "v_dashboard_interactions",
        "v_dashboard_opportunities",
        "v_dashboard_leads",
        "v_dashboard_contacts",
        "v_dashboard_customers",
    ):
        op.execute(f"DROP VIEW IF EXISTS {v} CASCADE;")

    # -- helper: team membership graph --------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION v_team_members(caller_id BIGINT)
        RETURNS TABLE(id BIGINT) AS $$
            -- caller themselves + every active user whose report_to_ids
            -- JSONB array contains caller_id (direct reports). Add a
            -- recursive variant later if multi-hop is needed.
            SELECT u.id FROM users u
            WHERE u.deleted = FALSE
              AND (u.id = caller_id OR u.report_to_ids @> to_jsonb(caller_id))
        $$ LANGUAGE sql STABLE;
        """
    )

    # -- customer (accounts) -------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_customers AS
        SELECT
            accounts_id          AS id,
            accounts_name        AS name,
            owner_by             AS owner_user_id,
            department_id,
            industry,
            status,
            deal_amount,
            deal_count,
            opportunity_amount,
            opportunity_count,
            contact_count,
            latest_deal_date     AS signed_at,
            latest_contact_date,
            next_contact_date,
            email,
            mobile,
            created_at,
            updated_at
        FROM accounts
        WHERE deleted = FALSE;
        """
    )

    # -- contact (contacts) --------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_contacts AS
        SELECT
            contacts_id     AS id,
            contacts_name   AS name,
            owner_by        AS owner_user_id,
            department_id,
            accounts_id,
            mobile,
            email,
            phone,
            gender,
            is_main,
            created_at,
            updated_at
        FROM contacts
        WHERE deleted = FALSE;
        """
    )

    # -- lead (leads) --------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_leads AS
        SELECT
            leads_id        AS id,
            leads_name      AS name,
            owner_by        AS owner_user_id,
            department_id,
            status,
            source,
            convertedstatus,
            convertedat,
            contact,
            phone,
            mobile,
            email,
            created_at,
            updated_at
        FROM leads
        WHERE deleted = FALSE;
        """
    )

    # -- opportunity (potentials) -------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_opportunities AS
        SELECT
            potentials_id        AS id,
            potentials_name      AS name,
            owner_by             AS owner_user_id,
            department_id,
            accounts_id,
            contacts_id,
            amount,
            status,
            milestone,
            closingdate,
            expected_close_date,
            actual_close_date,
            lost_reason,
            created_at,
            updated_at
        FROM potentials
        WHERE deleted = FALSE;
        """
    )

    # -- interaction --------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_interactions AS
        SELECT
            id,
            kind,
            occurred_at,
            source,
            -- actors[0].id may be a feishu user string (e.g. "alice") or a
            -- numeric users.id; only cast the numeric variant, NULL the rest
            -- so dashboards filtering on actor_user_id still works.
            CASE
                WHEN actors->0->>'id' ~ '^\d+$'
                THEN (actors->0->>'id')::bigint
                ELSE NULL
            END AS actor_user_id,
            subjects,
            content,
            provenance,
            created_at
        FROM interactions;
        """
    )

    # -- user ---------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_users AS
        SELECT
            id,
            username,
            realname,
            email,
            mobile,
            status,
            is_admin,
            department_id,
            role_id,
            report_to_ids,
            created_at,
            updated_at
        FROM users
        WHERE deleted = FALSE;
        """
    )

    # -- department ---------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_departments AS
        SELECT
            id,
            name,
            parent_id,
            sort_order,
            created_at,
            updated_at
        FROM departments;
        """
    )

    # -- ai_action (derived view over interactions) -------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_ai_actions AS
        SELECT
            i.id,
            COALESCE(i.content->>'action_type', i.kind) AS action_type,
            CASE
                WHEN i.actors->0->>'id' ~ '^\d+$'
                THEN (i.actors->0->>'id')::bigint
                ELSE NULL
            END AS actor_user_id,
            (i.content->>'accepted')::boolean           AS accepted,
            (i.content->>'recalled')::boolean           AS recalled,
            i.occurred_at,
            i.created_at
        FROM interactions i
        WHERE i.kind LIKE 'ai_%';
        """
    )

    # -- task ---------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_tasks AS
        SELECT
            tasks_id        AS id,
            tasks_name      AS name,
            owner_by        AS owner_user_id,
            department_id,
            accounts_id,
            contacts_id,
            status,
            priority,
            startdate,
            enddate,
            description,
            created_at,
            updated_at
        FROM tasks
        WHERE deleted = FALSE;
        """
    )

    # -- memory (memories from 0002_pgvector) -------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_memories AS
        SELECT
            id,
            user_id          AS owner_user_id,
            agent_id,
            scope,
            kind,
            title,
            content,
            confidence,
            created_at
        FROM memories;
        """
    )


def downgrade() -> None:
    for v in (
        "v_dashboard_memories",
        "v_dashboard_tasks",
        "v_dashboard_ai_actions",
        "v_dashboard_departments",
        "v_dashboard_users",
        "v_dashboard_interactions",
        "v_dashboard_opportunities",
        "v_dashboard_leads",
        "v_dashboard_contacts",
        "v_dashboard_customers",
    ):
        op.execute(f"DROP VIEW IF EXISTS {v};")
    op.execute("DROP FUNCTION IF EXISTS v_team_members(BIGINT);")
