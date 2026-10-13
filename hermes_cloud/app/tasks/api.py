"""Tasks REST API — Phase 4.0c.

Mounted at ``/api/tasks`` by :func:`main.create_app`.

Endpoints:

* ``GET /api/tasks`` — list tasks owned by the authed user (newest
  first). Optional ``?status=open|done|cancelled``.
* ``POST /api/tasks/{id}/done`` — mark complete.
* ``POST /api/tasks/{id}/cancel`` — soft-delete (撤销).

Soft-delete only: the row never leaves ``agent_tasks``.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from hermes_cloud.app.bridge.context import RequestContext, get_context
from hermes_cloud.app.tasks import (
    cancel_task,
    get_task,
    list_tasks,
    mark_done,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (_dt.datetime, _dt.date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.get("")
def list_my_tasks(
    status: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = 100,
    ctx: RequestContext = Depends(get_context),
) -> Dict[str, Any]:
    """List the authed user's tasks. Soft-deleted excluded by default."""
    rows = list_tasks(
        owner_user_id=int(ctx.user_id),
        status=status,
        include_deleted=include_deleted,
        limit=min(int(limit), 500),
    )
    return {
        "data": [_row_to_dict(r) for r in rows],
        "count": len(rows),
    }


@router.post("/{task_id}/done")
def post_mark_done(
    task_id: int,
    ctx: RequestContext = Depends(get_context),
) -> Dict[str, Any]:
    row = get_task(task_id)
    if not row or row.get("deleted"):
        raise HTTPException(status_code=404, detail="task not found")
    if row.get("owner_user_id") and int(row["owner_user_id"]) != int(ctx.user_id):
        raise HTTPException(status_code=403, detail="not your task")
    ok = mark_done(task_id)
    if not ok:
        raise HTTPException(status_code=409, detail="could not mark done")
    return {"data": _row_to_dict(get_task(task_id) or {})}


@router.post("/{task_id}/cancel")
def post_cancel(
    task_id: int,
    ctx: RequestContext = Depends(get_context),
) -> Dict[str, Any]:
    """Soft-delete a task (撤销)."""
    row = get_task(task_id)
    if not row or row.get("deleted"):
        raise HTTPException(status_code=404, detail="task not found")
    if row.get("owner_user_id") and int(row["owner_user_id"]) != int(ctx.user_id):
        raise HTTPException(status_code=403, detail="not your task")
    ok = cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=409, detail="could not cancel")
    return {"data": _row_to_dict(get_task(task_id) or {})}
