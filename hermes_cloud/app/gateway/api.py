"""Gateway REST API — approve / reject / revert / list actions.

Mounted at ``/api/gateway`` by :func:`main.create_app`. All endpoints
require dashboard auth (admin or the action's owning user).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status

from hermes_cloud.app.auth import require_dashboard_auth
from hermes_cloud.app.config import AppConfig
from hermes.crm.db import db_session
from hermes.action_gateway import dispatcher
from hermes_cloud.app.privacy.anonymizer import (
    _CN_ID_RE, _EMAIL_RE, _PHONE_RE,
)

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


def _redact_string(s: str) -> str:
    """Redact email / phone / Chinese national ID from a string.

    Approvals dashboards render gateway_actions.params verbatim; those
    params often include the free-text the agent pulled from a message
    (customer email, phone, ID). We mask before the response leaves the
    API so operators auditing approvals don't see raw PII that the
    training-export path already strips via anonymize_sample.
    """
    if not s:
        return s
    s = _EMAIL_RE.sub("[EMAIL]", s)
    s = _PHONE_RE.sub("[PHONE]", s)
    s = _CN_ID_RE.sub("[ID]", s)
    return s


def _redact_params(value: Any) -> Any:
    """Walk a params tree and redact PII in every string leaf."""
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {k: _redact_params(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_params(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_params(v) for v in value)
    return value


def _redact_action_row(row: dict) -> dict:
    """Redact the ``params`` field (and any other free-text JSON blob)
    in a gateway_actions row before it's returned to the dashboard."""
    if not row:
        return row
    out = dict(row)
    if "params" in out:
        out["params"] = _redact_params(out.get("params"))
    return out


def _auth(request: Request) -> dict:
    config = getattr(request.app.state, "config", None) or AppConfig()
    user_id, _ = require_dashboard_auth(request, config)
    # Build a user dict compatible with downstream callers
    if user_id is None:
        return {"id": 0, "is_admin": True}
    with db_session() as db:
        from hermes.crm.models.users_db import UsersDb
        row = UsersDb(db).get_user_by_id(int(user_id))
        if row:
            return row
    return {"id": int(user_id), "is_admin": False}


@router.get("/actions")
def list_actions(
    request: Request,
    state: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List gateway actions, optionally filtered by state."""
    user = _auth(request)
    # Internal kinds auto-execute on submit (see dispatcher
    # ``_FORCE_AUTO_EXECUTE_KINDS``); they never need a human gate, so
    # the approval inbox only surfaces egress kinds that actually require
    # review. History/revert for the auto-execute kinds lives elsewhere.
    _APPROVAL_INBOX_HIDDEN_KINDS = (
        "crm.write", "task.create", "coach.proposal",
        "memory.write", "lead.convert",
    )
    with db_session() as db:
        _placeholders = ", ".join(["%s"] * len(_APPROVAL_INBOX_HIDDEN_KINDS))
        clauses = [f"kind NOT IN ({_placeholders})"]
        args: list = list(_APPROVAL_INBOX_HIDDEN_KINDS)
        if state:
            clauses.append("state = %s")
            args.append(state)
        # Non-admin users only see their own actions
        if not user.get("is_admin"):
            clauses.append(
                "profile_id IN (SELECT id FROM profiles WHERE user_id = %s)"
            )
            args.append(int(user["id"]))

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.extend([min(int(limit), 100), max(int(offset), 0)])
        rows = db.fetchall(
            f"""
            SELECT id, profile_id, agent_name, kind, target, params,
                   state, trust_level, origin, created_at, executed_at,
                   reverted_at
            FROM gateway_actions
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            tuple(args),
        )
    rows = [_redact_action_row(r) for r in rows]
    return {"data": rows, "count": len(rows)}


@router.get("/actions/{action_id}")
def get_action(request: Request, action_id: str):
    _auth(request)
    with db_session() as db:
        row = db.fetchone(
            "SELECT * FROM gateway_actions WHERE id = %s",
            (action_id,),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Action not found")
    return {"data": _redact_action_row(row)}


@router.post("/actions/{action_id}/approve")
def approve_action(request: Request, action_id: str):
    user = _auth(request)
    try:
        # WebUI is the originating channel for this REST endpoint;
        # channel-bridge adapters call dispatcher.approve directly with
        # their own ``via_channel`` value.
        result = dispatcher.approve(
            action_id, int(user["id"]), via_channel="webui",
        )
        return {"data": result}
    except dispatcher.ActionWrongChannelError as exc:
        # Charter §1: the WebUI cannot approve a Feishu/WeCom-originated
        # action. Surface as 409 so the SPA can show "请在原渠道审批".
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/actions/{action_id}/reject")
def reject_action(request: Request, action_id: str):
    user = _auth(request)
    try:
        result = dispatcher.reject(
            action_id, int(user["id"]), via_channel="webui",
        )
        return {"data": result}
    except dispatcher.ActionWrongChannelError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/actions/{action_id}/revert")
def revert_action(request: Request, action_id: str):
    user = _auth(request)
    try:
        result = dispatcher.revert(action_id, int(user["id"]))
        return {"data": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
