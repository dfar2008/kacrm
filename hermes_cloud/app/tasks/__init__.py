"""Agent-task module — unified onto the CRM ``tasks`` module
(2026-05-07: agent_tasks table superseded; see migration in main).

Specialists (e.g. coach) emit follow-up tasks via
``proposed_actions=[{"kind": "task.create", ...}]``. The gateway
auto-executes (trust ``task.create`` defaults to level 3) which lands
a row in the CRM ``tasks`` data table — same module the SPA's
"客户事项" page reads — and returns a revert_token enabling soft-
delete via :func:`hermes.action_gateway.dispatcher.revert`.

Single source of truth: agent-emitted follow-ups and admin/sales-rep
manually-entered CRM tasks share one table. The user's "我的待办"
inbox is just a filtered view of ``tasks`` (owner_by=self,
status=open). Eliminates the historical agent_tasks/tasks split that
confused users and required dual-table writes.

Soft-delete only: ``cancel_task`` flips ``deleted=true``; the row is
preserved for audit. ``mark_done`` is the human "finished" affordance.

Field mapping vs the old agent_tasks shape:
  title             → tasks_name
  description       → description
  due_at            → due_at
  status            → status
  related_module    → related_module
  related_record_id → related_record_id
  source_agent      → source_agent
  gateway_action_id → gateway_action_id
  owner_user_id     → owner_by  +  created_by
  profile_id        → DROPPED (CRM scope is per-user via owner_by)
  source_draft_id   → DROPPED (rarely used)
  meta              → DROPPED (use related_module/record_id instead)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes.crm.db import DbWrapper, db_session

logger = logging.getLogger(__name__)


_VALID_STATUSES = {"open", "done", "cancelled"}


def create_task(
    *,
    profile_id: str,                # accepted for back-compat; unused
    title: str,
    owner_user_id: Optional[int] = None,
    description: Optional[str] = None,
    due_at: Optional[str] = None,
    related_module: Optional[str] = None,
    related_record_id: Optional[int] = None,
    source_agent: Optional[str] = None,
    source_draft_id: Optional[int] = None,  # accepted for back-compat
    gateway_action_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,  # accepted for back-compat
    db: Optional[DbWrapper] = None,
) -> int:
    """Insert one row in CRM ``tasks`` and return its id.

    ``due_at`` may be a string parseable by Postgres (ISO-8601) or NULL.
    """
    _ = (profile_id, source_draft_id, meta)  # silence unused warnings
    title = (title or "").strip()
    if not title:
        raise ValueError("create_task: 'title' is required")

    def _run(db: DbWrapper) -> int:
        row = db.fetchone(
            """
            INSERT INTO tasks
                (tasks_name, description, due_at, status,
                 related_module, related_record_id,
                 source_agent, gateway_action_id,
                 owner_by, created_by, updated_by)
            VALUES
                (%s, %s, %s, 'open',
                 %s, %s,
                 %s, %s,
                 %s, %s, %s)
            RETURNING tasks_id AS id
            """,
            (
                title[:255],
                description,
                due_at,
                related_module,
                int(related_record_id) if related_record_id else None,
                source_agent,
                gateway_action_id,
                int(owner_user_id) if owner_user_id else None,
                int(owner_user_id) if owner_user_id else None,
                int(owner_user_id) if owner_user_id else None,
            ),
        )
        return int(row["id"])

    if db is not None:
        return _run(db)
    with db_session() as owned:
        return _run(owned)


def cancel_task(
    task_id: int,
    *,
    db: Optional[DbWrapper] = None,
) -> bool:
    """Soft-delete a task. Returns True if a row was flipped."""

    def _run(db: DbWrapper) -> bool:
        row = db.fetchone(
            """
            UPDATE tasks
            SET deleted = true, updated_at = now()
            WHERE tasks_id = %s AND deleted = false
            RETURNING tasks_id
            """,
            (int(task_id),),
        )
        return row is not None

    if db is not None:
        return _run(db)
    with db_session() as owned:
        return _run(owned)


def mark_done(
    task_id: int,
    *,
    db: Optional[DbWrapper] = None,
) -> bool:
    """Mark a task as done (status='done')."""

    def _run(db: DbWrapper) -> bool:
        row = db.fetchone(
            """
            UPDATE tasks
            SET status = 'done', updated_at = now()
            WHERE tasks_id = %s AND deleted = false
            RETURNING tasks_id
            """,
            (int(task_id),),
        )
        return row is not None

    if db is not None:
        return _run(db)
    with db_session() as owned:
        return _run(owned)


def list_tasks(
    *,
    profile_id: Optional[str] = None,   # accepted for back-compat; unused
    owner_user_id: Optional[int] = None,
    status: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = 100,
    db: Optional[DbWrapper] = None,
) -> List[Dict[str, Any]]:
    """List tasks (newest first). Soft-deleted excluded by default.

    Returns rows projected back into the legacy agent_tasks shape so
    existing callers (the /api/tasks endpoint, the SPA inbox page)
    don't need to change column names.
    """
    _ = profile_id  # legacy arg, no longer used after CRM-table merge
    if status and status not in _VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")

    where: List[str] = []
    params: List[Any] = []
    if not include_deleted:
        where.append("deleted = false")
    if owner_user_id:
        where.append("(owner_by = %s OR created_by = %s)")
        params.append(int(owner_user_id))
        params.append(int(owner_user_id))
    if status:
        where.append("status = %s")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT tasks_id      AS id,
               NULL::text     AS profile_id,
               owner_by       AS owner_user_id,
               tasks_name     AS title,
               description, due_at, status,
               related_module, related_record_id, source_agent,
               NULL::bigint   AS source_draft_id,
               gateway_action_id::text AS gateway_action_id,
               '{{}}'::jsonb  AS meta,
               deleted, created_at, updated_at
        FROM tasks
        {where_sql}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(int(limit))

    def _run(db: DbWrapper) -> List[Dict[str, Any]]:
        return db.fetchall(sql, tuple(params)) or []

    if db is not None:
        return _run(db)
    with db_session() as owned:
        return _run(owned)


def get_task(task_id: int, *, db: Optional[DbWrapper] = None) -> Optional[Dict[str, Any]]:
    def _run(db: DbWrapper) -> Optional[Dict[str, Any]]:
        row = db.fetchone(
            "SELECT *, tasks_id AS id, tasks_name AS title, "
            "owner_by AS owner_user_id "
            "FROM tasks WHERE tasks_id = %s",
            (int(task_id),),
        )
        return row
    if db is not None:
        return _run(db)
    with db_session() as owned:
        return _run(owned)


__all__ = [
    "create_task",
    "cancel_task",
    "mark_done",
    "list_tasks",
    "get_task",
]
