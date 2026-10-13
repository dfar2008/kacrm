"""Metadata substrate for the CRM record layer.

kacrm ships two parallel field-metadata tables that cooperate at runtime:

  * ``collection_fields`` — (collection_name, name, JSON blob ``extra``). Drives
    form rendering. ``extra`` holds picklist options, linkage rules, relation
    targets, widget hints ("interface": "phone"|"email"|"richText"|...).
    Read by: form save/load, field CRUD admin, relation graph derivation.

  * ``module_fields`` — normalized columnar (field_label, field_type,
    is_hidden, is_searchable, is_summary, sort_order, ...). Drives the list
    query builder. Read by: ``ModuleFieldsDb.get_visible_fields()`` which
    dictates which columns the list endpoint SELECTs.

They intentionally complement rather than duplicate — forms need JSON
flexibility (arbitrary widget config); list queries need fast columnar reads.
We preserve both verbatim.

``module_views`` / ``module_view_fields`` sit on top of ``module_fields``:
  * ``module_views`` — a saved query (name, type='table'|'card'|..., filters
    JSON, role_ids JSON). "My Records", "Team Records", "My Created Records"
    are all rows here.
  * ``module_view_fields`` — per-view column ordering (which of the module's
    fields show up in THIS view, in what order, at what width).

PG deviations from kacrm MySQL:
  * All ``tinyint(1)`` boolean-semantic columns → ``BOOLEAN``.
  * ``datetime`` → ``TIMESTAMPTZ`` (plus the existing ``set_updated_at()``
    trigger wired in).
  * ``text``-typed JSON-carrying columns (``filters``, ``role_ids``) → ``JSONB``
    so filters can be queried with ``@>`` / ``->>`` if we ever want to.
  * ``options`` on ``module_fields`` stays ``TEXT`` because kacrm sometimes
    stores non-JSON CSV here (legacy picklists). Forcing JSONB would reject
    those rows on seed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-15
"""
from __future__ import annotations

from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # collection_fields — form-layer metadata (JSON blob per field).
    # Keyed by (collection_name, name) so it's collection-name addressable
    # even when the collections row hasn't loaded yet.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE collection_fields (
            id              BIGSERIAL PRIMARY KEY,
            collection_name VARCHAR(255) NOT NULL,
            name            VARCHAR(255) NOT NULL,
            extra           JSONB NOT NULL,
            UNIQUE (collection_name, name)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_collection_fields_by_collection "
        "ON collection_fields(collection_name);"
    )

    # ------------------------------------------------------------------
    # module_fields — list-layer metadata (normalized columns).
    # ``module_id`` references ``collections.id`` (kacrm called it ``modules``
    # but the table merged into our ``collections`` registry).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE module_fields (
            id                 BIGSERIAL PRIMARY KEY,
            module_id          BIGINT NOT NULL
                               REFERENCES collections(id) ON DELETE CASCADE,
            field_label        VARCHAR(100) NOT NULL,
            field_name         VARCHAR(100) NOT NULL,
            field_type         VARCHAR(50)  NOT NULL,
            field_length       INTEGER,
            is_required        BOOLEAN NOT NULL DEFAULT FALSE,
            is_duplicate_check BOOLEAN NOT NULL DEFAULT FALSE,
            default_value      TEXT,
            options            TEXT,
            description        TEXT,
            sort_order         INTEGER NOT NULL DEFAULT 0,
            is_system          BOOLEAN NOT NULL DEFAULT FALSE,
            is_default         BOOLEAN NOT NULL DEFAULT FALSE,
            is_hidden          BOOLEAN NOT NULL DEFAULT FALSE,
            is_readonly        BOOLEAN NOT NULL DEFAULT FALSE,
            is_summary         BOOLEAN NOT NULL DEFAULT FALSE,
            is_searchable      BOOLEAN NOT NULL DEFAULT TRUE,
            datainfo           VARCHAR(200),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (module_id, field_name)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_module_fields_module ON module_fields(module_id);"
    )
    op.execute(
        "CREATE TRIGGER trg_module_fields_updated_at "
        "BEFORE UPDATE ON module_fields "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # module_views — saved queries per module.
    # Default kacrm views: My Records / My Created Records / Subuser Records /
    # Subuser Created Records / Team Records.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE module_views (
            id          BIGSERIAL PRIMARY KEY,
            module_id   BIGINT NOT NULL
                        REFERENCES collections(id) ON DELETE CASCADE,
            name        VARCHAR(100) NOT NULL,
            type        VARCHAR(20)  NOT NULL DEFAULT 'table',
            filters     JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_default  BOOLEAN NOT NULL DEFAULT FALSE,
            is_public   BOOLEAN NOT NULL DEFAULT TRUE,
            role_ids    JSONB,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_by  BIGINT NOT NULL REFERENCES users(id) ON DELETE SET NULL
                        DEFERRABLE INITIALLY DEFERRED,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT module_views_type_check
                CHECK (type IN ('table', 'card', 'kanban', 'chart', 'calendar'))
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_module_views_module ON module_views(module_id);"
    )
    op.execute(
        "CREATE INDEX idx_module_views_created_by ON module_views(created_by);"
    )
    op.execute(
        "CREATE TRIGGER trg_module_views_updated_at "
        "BEFORE UPDATE ON module_views "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # module_view_fields — per-view column config. A view reuses module_fields
    # definitions but chooses which to show + their order + their width.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE module_view_fields (
            id          BIGSERIAL PRIMARY KEY,
            view_id     BIGINT NOT NULL
                        REFERENCES module_views(id) ON DELETE CASCADE,
            field_id    BIGINT NOT NULL
                        REFERENCES module_fields(id) ON DELETE CASCADE,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            width       INTEGER,
            UNIQUE (view_id, field_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_module_view_fields_view ON module_view_fields(view_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS module_view_fields;")
    op.execute("DROP TABLE IF EXISTS module_views;")
    op.execute("DROP TABLE IF EXISTS module_fields;")
    op.execute("DROP TABLE IF EXISTS collection_fields;")
