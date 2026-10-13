"""Add agent-task columns to ``tasks`` (unify with agent_tasks).

Companion to commit 7142990 (2026-05-07 "tasks: merge agent_tasks →
CRM tasks module"). That commit rewrote
``hermes_cloud/app/tasks/__init__.py`` to INSERT into ``tasks`` with
``due_at, related_module, related_record_id, source_agent,
gateway_action_id`` — but **forgot to write the alembic migration**.
Result: every `task.create` from the agent crashes with
``column "due_at" does not exist`` on prod, and the polymorphic
relation ``related_module / related_record_id`` (which `crm_list_related`
needs for ``potentials → tasks`` and ``leads → tasks``) never existed.

Also registers the 5 new columns in ``module_fields`` so the SPA
admin schema page surfaces them (also promised by 7142990).

Backfill: existing rows that point at an account via ``accounts_id``
get ``related_module='accounts', related_record_id=accounts_id`` so the
new polymorphic path returns them too (NULL rows stay NULL).

Idempotent: ``IF NOT EXISTS`` everywhere.

Revision ID: 0094
Revises: 0093
Create Date: 2026-05-31
"""
from __future__ import annotations

from alembic import op


revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


_NEW_COLUMNS = (
    ("due_at",            "TIMESTAMPTZ"),
    ("related_module",    "TEXT"),
    ("related_record_id", "BIGINT"),
    ("source_agent",      "TEXT"),
    ("gateway_action_id", "UUID"),
)


# module_fields rows for the SPA admin schema page. Keep aligned with
# the existing tasks module field shape (see 0011_crm_modules + 0066).
_NEW_FIELDS = (
    # (field_name,        field_type, field_label,    sort_order)
    ("due_at",             "datetime", "截止时间",       100),
    ("related_module",     "text",     "关联模块",       101),
    ("related_record_id",  "number",   "关联记录 ID",    102),
    ("source_agent",       "text",     "来源 Agent",     103),
    ("gateway_action_id",  "text",     "Gateway Action", 104),
)


def upgrade() -> None:
    for col, sql_type in _NEW_COLUMNS:
        op.execute(
            f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {col} {sql_type}"
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_related "
        "ON tasks (related_module, related_record_id) "
        "WHERE deleted = false"
    )

    # Backfill: existing CRM tasks linked via accounts_id/contacts_id
    # become discoverable via the polymorphic path too. Prefer accounts
    # (one task usually belongs to one deal/account context).
    op.execute(
        """
        UPDATE tasks
           SET related_module = 'accounts',
               related_record_id = accounts_id
         WHERE related_module IS NULL
           AND accounts_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE tasks
           SET related_module = 'contacts',
               related_record_id = contacts_id
         WHERE related_module IS NULL
           AND contacts_id IS NOT NULL
        """
    )

    # Register the 5 new fields in module_fields so /app/crm admin
    # schema page surfaces them. is_system=true to lock against
    # user-driven deletion (these back the agent task pipeline).
    for fname, ftype, flabel, sort in _NEW_FIELDS:
        op.execute(
            f"""
            INSERT INTO module_fields
                (module_id, field_name, field_label, field_type,
                 is_required, is_duplicate_check, sort_order,
                 is_system, is_default, is_hidden, is_readonly,
                 is_summary, is_searchable, created_at, updated_at)
            SELECT m.id, '{fname}', '{flabel}', '{ftype}',
                   false, false, {sort},
                   true,  false, false, true,
                   false, true,  now(),  now()
              FROM modules m
             WHERE m.table_name = 'tasks'
               AND NOT EXISTS (
                   SELECT 1 FROM module_fields mf
                    WHERE mf.module_id = m.id
                      AND mf.field_name = '{fname}'
               )
            """
        )


def downgrade() -> None:
    # Remove the module_fields rows first (so they don't outlive the cols).
    for fname, _ftype, _flabel, _sort in _NEW_FIELDS:
        op.execute(
            f"""
            DELETE FROM module_fields
             WHERE field_name = '{fname}'
               AND module_id IN (
                   SELECT id FROM modules WHERE table_name = 'tasks'
               )
            """
        )

    op.execute("DROP INDEX IF EXISTS idx_tasks_related")

    for col, _sql_type in reversed(_NEW_COLUMNS):
        op.execute(f"ALTER TABLE tasks DROP COLUMN IF EXISTS {col}")
