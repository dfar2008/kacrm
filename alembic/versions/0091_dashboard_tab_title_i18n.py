"""Add ``title_i18n`` to ``dashboard_tabs`` for bilingual preset names.

Preset tabs ("我的工作" / "团队看板" / "组织全景") get materialized into
``dashboard_tabs.title`` on a user's first visit, snapshotting the
locale active at that moment. That left users stuck reading Chinese
labels after toggling the SPA to English (and vice-versa), because
the title column is a plain string with no per-locale storage.

This migration adds a JSONB ``title_i18n`` column shaped
``{locale: text}`` and backfills it for every existing preset-seeded
tab by joining on ``preset_key`` and reading the new
``_PRESET_TAB_TITLES`` map. User-created tabs (preset_key NULL) and
preset tabs the admin already renamed away from the default both stay
``title_i18n = NULL`` so the routes layer falls back to the raw
``title`` — i.e. a user's chosen name is never overwritten.

Revision ID: 0091
Revises: 0090
Create Date: 2026-05-27
"""
from alembic import op


revision = "0091"
down_revision = "0090"


# Mirror of ``hermes_cloud.app.dashboards.presets._PRESET_TAB_TITLES``.
# Duplicated here (rather than imported) so the migration stays valid
# even if the preset map is later renamed / restructured — alembic
# revisions are immutable history and must not import app code that
# can drift.
_PRESET_TAB_TITLES = {
    "my_work": {"zh-CN": "我的工作",   "en-US": "My Work"},
    "team":    {"zh-CN": "团队看板",   "en-US": "Team Dashboard"},
    "org":     {"zh-CN": "组织全景",   "en-US": "Organization Overview"},
}


def upgrade() -> None:
    op.execute(
        "ALTER TABLE dashboard_tabs "
        "ADD COLUMN IF NOT EXISTS title_i18n JSONB"
    )

    # Backfill: walk each known preset suffix and stamp the bilingual
    # title onto matching tabs. Only update rows whose current ``title``
    # still matches one of the configured translations — that means the
    # user hasn't renamed away from the default. Renamed tabs keep
    # ``title_i18n = NULL`` so the route resolver falls back to
    # ``title``.
    for suffix, mapping in _PRESET_TAB_TITLES.items():
        candidates = [v for v in mapping.values() if v]
        if not candidates:
            continue
        op.execute(
            f"""
            UPDATE dashboard_tabs
               SET title_i18n = '{_json(mapping)}'::jsonb
             WHERE preset_key LIKE '%.{suffix}'
               AND title = ANY (ARRAY[{_sql_array(candidates)}])
               AND title_i18n IS NULL
            """
        )


def downgrade() -> None:
    op.execute("ALTER TABLE dashboard_tabs DROP COLUMN IF EXISTS title_i18n")


# ---- small inline helpers (kept local — no shared util) ----------------


def _json(d) -> str:
    """JSON-encode without importing json at module level; embedded
    directly in the SQL string so we don't need parameterized exec.
    Values come from a curated literal map above, never user input."""
    import json as _json_mod
    return _json_mod.dumps(d, ensure_ascii=False).replace("'", "''")


def _sql_array(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)
