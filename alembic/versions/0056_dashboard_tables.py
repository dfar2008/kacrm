"""Dashboards feature: dashboard_tabs + dashboard_charts + user_grants.

Spec: docs/superpowers/specs/2026-05-11-ai-dashboard-design.md §七

Three tables:
- dashboard_tabs: user-owned (or shared) tab containers; preset_key
  references hermes_cloud/app/dashboards/presets.py templates (string,
  not FK because preset templates live in code, not the DB).
- dashboard_charts: chart configs (DSL JSONB) with FK CASCADE to tab.
- user_grants: per-user explicit grants on top of role defaults
  (see hermes_cloud/app/dashboards/grants.py).

Style follows the rest of alembic/versions/*: raw SQL via op.execute,
revision IDs are bare integers ("0056").

Revision ID: 0056
Revises: 0055
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op


revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dashboard_tabs (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title             TEXT NOT NULL,
            position          INTEGER NOT NULL DEFAULT 0,
            visibility        TEXT NOT NULL DEFAULT 'private',
            shared_with       JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_system_preset  BOOLEAN NOT NULL DEFAULT FALSE,
            preset_key        TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_dashboard_visibility
              CHECK (visibility IN ('private', 'shared')),
            CONSTRAINT chk_dashboard_shared_with_when_shared
              CHECK (visibility = 'private' OR shared_with IS NOT NULL)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_dashboard_tabs_owner "
        "ON dashboard_tabs(owner_user_id, position);"
    )

    op.execute(
        """
        CREATE TABLE dashboard_charts (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tab_id                   UUID NOT NULL
                                     REFERENCES dashboard_tabs(id) ON DELETE CASCADE,
            position                 INTEGER NOT NULL DEFAULT 0,
            title                    TEXT NOT NULL,
            dsl                      JSONB NOT NULL,
            source_prompt_message_id UUID,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_dashboard_charts_tab "
        "ON dashboard_charts(tab_id, position);"
    )

    op.execute(
        """
        CREATE TABLE user_grants (
            user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            grant_name  TEXT NOT NULL,
            granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            granted_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
            PRIMARY KEY (user_id, grant_name)
        );
        """
    )

    # updated_at triggers (consistent with rest of schema — see
    # set_updated_at() defined in 0001_golden_schema.py).
    op.execute(
        "CREATE TRIGGER trg_dashboard_tabs_updated_at "
        "BEFORE UPDATE ON dashboard_tabs "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )
    op.execute(
        "CREATE TRIGGER trg_dashboard_charts_updated_at "
        "BEFORE UPDATE ON dashboard_charts "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_grants;")
    op.execute("DROP INDEX IF EXISTS idx_dashboard_charts_tab;")
    op.execute("DROP TABLE IF EXISTS dashboard_charts;")
    op.execute("DROP INDEX IF EXISTS idx_dashboard_tabs_owner;")
    op.execute("DROP TABLE IF EXISTS dashboard_tabs;")
