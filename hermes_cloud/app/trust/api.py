"""Trust level REST API — view / set trust levels.

Mounted at ``/api/trust`` by :func:`main.create_app`. Admin-only for
setting levels; any authenticated user can view their own profile's
trust snapshot.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from hermes_cloud.app.auth import require_admin_auth, require_dashboard_auth
from hermes_cloud.app.config import AppConfig
from hermes.crm.db import db_session
from hermes.trust import engine


def _get_config(request: Request) -> AppConfig:
    return getattr(request.app.state, "config", None) or AppConfig()

router = APIRouter(prefix="/api/trust", tags=["trust"])


@router.get("/levels/{profile_id}")
def get_levels(request: Request, profile_id: str):
    """Get all trust levels for a profile."""
    require_dashboard_auth(request, _get_config(request))
    with db_session() as db:
        rows = db.fetchall(
            """
            SELECT agent_name, action_kind, level, rationale, updated_at
            FROM trust_levels
            WHERE profile_id = %s
            ORDER BY agent_name, action_kind
            """,
            (profile_id,),
        )
    return {"data": rows}


@router.put("/levels/{profile_id}/{agent_name}/{action_kind}")
async def set_level(
    request: Request,
    profile_id: str,
    agent_name: str,
    action_kind: str,
):
    """Set a trust level (admin only)."""
    admin_user_id = require_admin_auth(request, _get_config(request))
    try:
        body = await request.json()
    except Exception:
        body = {}

    level = body.get("level")
    if level is None:
        raise HTTPException(status_code=400, detail="level is required")
    level = int(level)

    rationale = body.get("rationale", "")
    try:
        engine.set_level(
            profile_id, agent_name, action_kind, level,
            rationale=rationale,
            updated_by=int(admin_user_id) if str(admin_user_id).isdigit() else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"success": True, "level": level}


@router.get("/snapshot/{profile_id}")
def trust_snapshot(request: Request, profile_id: str):
    """Get a trust snapshot keyed by (agent, action_kind) for the UI."""
    require_dashboard_auth(request, _get_config(request))
    with db_session() as db:
        rows = db.fetchall(
            """
            SELECT agent_name, action_kind, level
            FROM trust_levels
            WHERE profile_id = %s
            """,
            (profile_id,),
        )
    snapshot: dict = {}
    for r in rows:
        agent = r["agent_name"]
        if agent not in snapshot:
            snapshot[agent] = {}
        snapshot[agent][r["action_kind"]] = r["level"]
    return {"data": snapshot}


@router.get("/decisions")
def list_decisions(
    request: Request,
    profile_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    action_kind: Optional[str] = None,
    decision: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List recent trust decisions for the audit trail.

    Each row joins ``trust_decisions`` → ``gateway_actions`` so the
    caller gets ``agent_name`` / ``action_kind`` / ``target`` for free.
    The dashboard's TrustTab renders this as a timeline under the
    level-snapshot grid; operators use it to answer "why did the
    approval gate fire / not fire on this action?".

    Non-admins are filtered to their own profiles; admins see all.
    """
    user_id, _ = require_dashboard_auth(request, _get_config(request))

    clauses: list = []
    args: list = []

    if profile_id:
        clauses.append("ga.profile_id = %s")
        args.append(profile_id)
    if agent_name:
        clauses.append("ga.agent_name = %s")
        args.append(agent_name)
    if action_kind:
        clauses.append("ga.kind = %s")
        args.append(action_kind)
    if decision:
        clauses.append("td.decision = %s")
        args.append(decision)

    with db_session() as db:
        # Admin gate: non-admins only see decisions on actions whose
        # profile belongs to a profile_id they own.
        is_admin = False
        if user_id is not None:
            from hermes.crm.models.users_db import UsersDb
            me = UsersDb(db).get_user_by_id(int(user_id))
            is_admin = bool(me and me.get("is_admin"))
        if not is_admin:
            clauses.append(
                "ga.profile_id IN (SELECT id FROM profiles WHERE user_id = %s)"
            )
            args.append(int(user_id) if user_id else 0)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.extend([int(limit), int(offset)])
        rows = db.fetchall(
            f"""
            SELECT td.id,
                   td.action_id,
                   td.trust_level_snapshot,
                   td.decision,
                   td.reason,
                   td.decided_at,
                   ga.agent_name,
                   ga.kind         AS action_kind,
                   ga.profile_id,
                   ga.state        AS action_state,
                   ga.target
            FROM trust_decisions td
            LEFT JOIN gateway_actions ga ON ga.id = td.action_id
            {where}
            ORDER BY td.decided_at DESC
            LIMIT %s OFFSET %s
            """,
            tuple(args),
        ) or []

    # Aggregate a small summary for the header tiles (same window).
    by_decision: dict = {}
    by_agent: dict = {}
    for r in rows:
        d = r.get("decision") or "unknown"
        by_decision[d] = by_decision.get(d, 0) + 1
        a = r.get("agent_name") or "unknown"
        by_agent[a] = by_agent.get(a, 0) + 1
        # FastAPI can't serialize datetime directly via fetchall dicts
        if r.get("decided_at") is not None:
            r["decided_at"] = str(r["decided_at"])

    return {
        "data": rows,
        "count": len(rows),
        "summary": {"by_decision": by_decision, "by_agent": by_agent},
    }


@router.post("/auto-adjust")
def trigger_auto_adjust(request: Request):
    """Trigger trust level auto-adjustment (admin only).

    Normally runs as a daily Celery task. This endpoint allows manual
    triggering for testing / operational use.
    """
    require_admin_auth(request, _get_config(request))
    from hermes.trust.auto_adjust import auto_adjust_all

    adjustments = auto_adjust_all()
    return {
        "adjustments": adjustments,
        "count": len(adjustments),
    }
