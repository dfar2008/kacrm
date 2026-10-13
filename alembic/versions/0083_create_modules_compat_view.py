"""Formalize the ``modules`` backward-compat view.

History — silent deployment bug
-------------------------------
``modules`` is a thin view that projects ``collections`` into the
shape kacrm-era SQL expects (``id, name, table_name, label``).
``module_fields`` / ``module_views`` / ``module_view_fields`` /
``ModulesDb`` all read through it; the naming pair
``modules`` ↔ ``module_fields`` is the conscious convention.

The view existed on the dev DB (created out-of-band via ``psql`` long
ago) but **no migration ever recreated it** — verified via
``git log -S "VIEW modules"`` and full repo grep. Fresh DB clones
(prod, CI, new dev laptops) therefore lacked the view, and any code
path with ``FROM modules`` raised ``relation "modules" does not exist``.

Confirmed hot paths that 500 without this view:
  * ``FieldWriteback.list_rules`` (admin writeback admin UI)
  * ``approval/engine.py`` pending-approvals query
  * ``hermes_cloud/app/interactions/api.py`` module-name → table-name resolve
  * historical migrations 0068 / 0069 (label_i18n + option_labels
    upgrades) — these would error during fresh ``alembic upgrade head``

This migration formalises the view body that's already running on dev,
so fresh installs match. ``CREATE OR REPLACE`` makes it idempotent on
DBs that already have it.

Revision ID: 0083
Revises: 0082
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


# Body matches what ``pg_get_viewdef`` returns on the existing dev DB.
# ``name AS table_name`` and ``title AS label`` are the actual purpose
# of the view — the other four hardcoded columns (is_hidden / icon /
# is_default / is_form) are kacrm-era schema baggage that no current
# reader queries; kept here so the view stays drop-in compatible with
# any future code that does happen to ``SELECT *``.
_VIEW_SQL = """
CREATE OR REPLACE VIEW modules AS
SELECT id,
       name,
       name        AS table_name,
       title       AS label,
       0           AS is_hidden,
       id          AS sort_order,
       'fa-folder'::text AS icon,
       0           AS is_default,
       0           AS is_form
  FROM collections
"""


def upgrade() -> None:
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS modules")
