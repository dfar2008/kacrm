"""HTTP routes for the team audit dashboard — Phase 5.6.

Exposes 5 endpoints under ``/api/insights``. Mounted by main.py
during the Phase-2 router-mount block, same place as
``copilot.routes.register_routes``.

Auth: same pattern as copilot/routes — bearer + crm session cookie
fallback. Non-admin scope='org' calls auto-downgrade to 'team'
(``viewable_user_ids`` enforces this). 403 if scope='org' explicitly
requested by non-admin (route-level precheck).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from hermes.crm.db import DbWrapper, get_db

from hermes.crm.insights.team_audit import (
    ai_actions_summary,
    pipeline_funnel,
    sleeping_deals_trend,
)

from hermes_cloud.app.scope import ScopeKind, viewable_user_ids
from hermes_cloud.app.scope.audit import record_scope_access

logger = logging.getLogger(__name__)


def _resolve_uids(
    db: DbWrapper, *, caller_id: int, scope: ScopeKind,
    resource_kind: str, owner_id: Optional[int] = None,
) -> List[int]:
    """Resolve viewable user_ids + audit the access.

    Multi-tenant RBAC helper — couples to ``hermes_cloud.app.scope``
    (cloud-only). The insights panels themselves are now hermes-native
    and scope-agnostic; this route layer resolves + audits scope and
    hands a plain ``uids: List[int]`` into them.

    v1.7: ``owner_id`` (if provided) further narrows the set to a
    single user — but the user MUST be in the caller's viewable set,
    otherwise we silently fall back to the broader scope (no privilege
    escalation). Audit row marks this as scope_kind='team' when active.
    """
    allowed = viewable_user_ids(db, caller_id=caller_id, scope=scope)
    if owner_id is not None and int(owner_id) in allowed:
        # Specific-user filter: narrow to exactly this user + audit.
        record_scope_access(
            db, caller_id=caller_id, scope_kind="team",
            resource_kind=resource_kind,
            resource_filter={"specific_user_id": int(owner_id)},
        )
        return [int(owner_id)]
    if scope != "self":
        record_scope_access(
            db, caller_id=caller_id, scope_kind=scope,
            resource_kind=resource_kind,
        )
    return sorted(allowed)


def _user_id_from_request(request: Request, app: Any) -> int:
    """Same auth resolver as channel/preferences_api."""
    try:
        from hermes_cloud.app.auth import require_bearer_auth
        cfg = app.state.config
        uid = require_bearer_auth(request, cfg)
        if uid:
            return int(uid)
    except Exception:
        pass
    try:
        from hermes_cloud.app.bridge.identity import current_user as bridge_current_user
        account = bridge_current_user(request)
        return int(account.id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="not authenticated")


def _is_admin(db: DbWrapper, user_id: int) -> bool:
    row = db.fetchone(
        "SELECT is_admin FROM users WHERE id = %s", (int(user_id),),
    )
    return bool(row and row.get("is_admin"))


def _validate_scope(scope: str) -> str:
    if scope not in ("self", "team", "org"):
        raise HTTPException(
            status_code=422,
            detail=f"scope must be one of self/team/org, got {scope!r}",
        )
    return scope


def register_routes(app: Any) -> None:
    router = APIRouter(prefix="/api/insights", tags=["insights"])

    @router.get("/ai_actions_summary")
    def get_ai_actions(
        request: Request,
        scope: str = Query("self"),
        owner_id: int | None = Query(None),
        week_offset: int = Query(0),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        user_id = _user_id_from_request(request, app)
        scope = _validate_scope(scope)
        if scope == "org" and not _is_admin(db, user_id):
            raise HTTPException(403, "scope='org' is admin-only")
        uids = _resolve_uids(
            db, caller_id=user_id, scope=scope, owner_id=owner_id,
            resource_kind="dashboard_ai_calendar",
        )
        return ai_actions_summary(
            db, uids=uids, week_offset=int(week_offset),
        )

    @router.get("/sleeping_deals_trend")
    def get_sleeping_trend(
        request: Request,
        scope: str = Query("self"),
        owner_id: int | None = Query(None),
        weeks: int = Query(4, ge=1, le=12),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        user_id = _user_id_from_request(request, app)
        scope = _validate_scope(scope)
        if scope == "org" and not _is_admin(db, user_id):
            raise HTTPException(403, "scope='org' is admin-only")
        uids = _resolve_uids(
            db, caller_id=user_id, scope=scope, owner_id=owner_id,
            resource_kind="dashboard_sleeping_trend",
        )
        return {
            "weeks": sleeping_deals_trend(
                db, uids=uids, weeks=int(weeks),
            ),
        }

    @router.get("/team_pipeline_funnel")
    def get_pipeline_funnel(
        request: Request,
        scope: str = Query("team"),
        owner_id: int | None = Query(None),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        user_id = _user_id_from_request(request, app)
        scope = _validate_scope(scope)
        if scope == "org" and not _is_admin(db, user_id):
            raise HTTPException(403, "scope='org' is admin-only")
        uids = _resolve_uids(
            db, caller_id=user_id, scope=scope, owner_id=owner_id,
            resource_kind="dashboard_pipeline_funnel",
        )
        return {
            "stages": pipeline_funnel(db, uids=uids),
        }

    @router.get("/team_activity/{module}/{record_id}")
    def get_team_activity(
        request: Request,
        module: str,
        record_id: int,
        limit: int = Query(10, ge=1, le=50),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        """Phase 5.2.B — record-detail "团队成员谁也跟过" card data.

        Returns the most recent interactions on this record by users
        OTHER than the caller — *regardless of the actor's current
        team membership or employment status*. Plan v1.4 (2026-05-10
        user feedback): "历史跟着 record 走，新接手的销售看得到全部
        前任的痕迹"。

        Visibility model:
          1. Caller must have access to the record itself (same gate
             as /crm/records/{module}/{id} detail page — owner_by /
             created_by within caller's team scope, OR admin).
          2. If 1 passes, return ALL interactions on the record where
             actor.user.id != caller_id. Includes contributions from
             departed employees (status=0), members reassigned to
             other depts, etc. — the record's history is the record's
             history.

        Plan v1.3 already removed the ``channel:* + kind=message``
        privacy filter (客户沟通即业务数据). v1.4 drops the
        actor-must-be-current-team filter for the same reason: an
        interaction's value travels with the record, not the actor.
        """
        import json as _json
        user_id = _user_id_from_request(request, app)
        from hermes_cloud.app.scope import viewable_user_ids
        from hermes_cloud.app.scope.audit import record_scope_access

        # ── Step 1: caller must be able to see this record ──────────
        # Mirror the same row-level enforcement as
        # crm/routes:detail_record so users can't deep-link to records
        # outside their scope just by hitting this endpoint.
        is_admin_row = db.fetchone(
            "SELECT is_admin FROM users WHERE id = %s", (user_id,),
        )
        is_admin = bool(is_admin_row and is_admin_row.get("is_admin"))
        if not is_admin:
            try:
                row = db.fetchone(
                    f'SELECT owner_by, created_by FROM "{module}" '
                    f'WHERE "{module}_id" = %s '
                    f'  AND (deleted = FALSE OR deleted IS NULL)',
                    (int(record_id),),
                )
            except Exception:
                # Module may not have *_id column or table; treat as not found
                logger.exception("team_activity record-access check failed")
                return {"activities": [], "scope_size": 0}
            if row is None:
                return {"activities": [], "scope_size": 0}
            try:
                viewable = viewable_user_ids(
                    db, caller_id=user_id, scope="team",
                )
            except Exception:
                viewable = {user_id}
            if not (row.get("owner_by") in viewable
                    or row.get("created_by") in viewable):
                # Don't 404 (avoid leaking record existence) — just
                # return empty payload, same shape SPA expects.
                return {"activities": [], "scope_size": 0}

        # Audit team-scope read on the record (record-level, not
        # actor-level — the access we're auditing is "manager looked
        # at history of this customer record", not "team scope query").
        record_scope_access(
            db, caller_id=user_id,
            scope_kind="team" if not is_admin else "org",
            resource_kind="record_team_activity",
            resource_filter={"module": module, "record_id": record_id},
        )
        try:
            db.commit()
        except Exception:
            pass

        # ── Step 2: ALL interactions on the record, minus caller's own ──
        subj_filter = _json.dumps([{"module": module, "id": int(record_id)}])
        try:
            rows = db.fetchall(
                """
                SELECT id, kind, occurred_at, source,
                       (actors->0->>'id')::bigint AS actor_user_id,
                       content::text AS content_text
                  FROM interactions
                 WHERE subjects @> %s::jsonb
                   AND (actors->0->>'type') = 'user'
                   AND ((actors->0->>'id')::bigint != %s
                        OR (actors->0->>'id') IS NULL)
                 ORDER BY occurred_at DESC
                 LIMIT %s
                """,
                (subj_filter, int(user_id), limit),
            ) or []
        except Exception:
            logger.exception("team_activity query failed")
            try:
                db.rollback()
            except Exception:
                pass
            return {"activities": [], "scope_size": 0}

        activities = []
        for r in rows:
            try:
                c = _json.loads(r["content_text"]) if r.get("content_text") else {}
            except Exception:
                c = {}
            text = c.get("text") or c.get("agent_reply") or c.get("user_message") or ""
            activities.append({
                "id": str(r["id"]),
                "kind": r["kind"],
                "source": r["source"],
                "occurred_at": (
                    r["occurred_at"].isoformat() if r.get("occurred_at") else None
                ),
                "user_id": int(r["actor_user_id"]) if r.get("actor_user_id") else None,
                "text": str(text)[:200],
            })
        # ``scope_size`` semantic post-v1.4: number of distinct
        # non-caller actors in the returned slice (caps at ``limit``).
        # SPA may use this for "you + N others" hint text. Doesn't
        # affect the data itself; legacy callers expecting an integer
        # still get one.
        distinct_actors = {
            int(r["actor_user_id"]) for r in rows
            if r.get("actor_user_id")
        }
        return {
            "activities": activities,
            "scope_size": len(distinct_actors),
        }

    @router.get("/team_members")
    def get_team_members(
        request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        """Phase 5.2.A v1.7 — list users the caller may pick as a
        scope filter (i.e. the user's team scope). Admin gets all
        active users; non-admin manager gets self + recursive
        subordinates; non-manager gets self only.

        Used by SPA scope segment dropdown to populate "指定成员".
        """
        caller_id = _user_id_from_request(request, app)
        from hermes_cloud.app.scope import viewable_user_ids
        try:
            allowed = viewable_user_ids(
                db, caller_id=caller_id,
                scope="org" if _is_admin(db, caller_id) else "team",
            )
        except Exception:
            allowed = {caller_id}
        if not allowed:
            return {"members": []}
        try:
            rows = db.fetchall(
                """
                SELECT id, realname, username, department_id
                  FROM users
                 WHERE id = ANY(%s::bigint[])
                   AND deleted = FALSE
                   AND (status IS NULL OR status = 1)
                 ORDER BY (id = %s) DESC, realname, id
                """,
                (sorted(allowed), caller_id),
            ) or []
        except Exception:
            logger.exception("team_members query failed")
            return {"members": []}
        return {
            "members": [
                {
                    "id": int(r["id"]),
                    "name": (r.get("realname") or r.get("username") or f"#{r['id']}").strip(),
                    "department_id": r.get("department_id"),
                    "is_self": int(r["id"]) == caller_id,
                }
                for r in rows
            ],
        }

    @router.get("/team_brief_latest")
    def get_team_brief_latest(
        request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        """Phase 5.2.C — most-recent auto_drive.team_tick interaction
        for the caller (manager). Used by the SPA Copilot panel's team
        morning brief section.

        Returns ``{"tick": {...} | null}``. tick.content carries the
        run summary (manager_id, member_count, signals_seen,
        actions_planned, actions_executed, team_signals_count,
        dry_run, version).
        """
        import json as _json
        user_id = _user_id_from_request(request, app)
        try:
            row = db.fetchone(
                """
                SELECT id, kind, occurred_at, content::text AS c
                  FROM interactions
                 WHERE kind = 'auto_drive.team_tick'
                   AND content @> %s::jsonb
                 ORDER BY occurred_at DESC
                 LIMIT 1
                """,
                (_json.dumps({"manager_id": int(user_id)}),),
            )
        except Exception:
            logger.exception("team_brief_latest query failed")
            try: db.rollback()
            except Exception: pass
            return {"tick": None}

        if not row:
            return {"tick": None}
        try:
            content = _json.loads(row["c"]) if row.get("c") else {}
        except Exception:
            content = {}
        return {
            "tick": {
                "id": str(row["id"]),
                "occurred_at": (
                    row["occurred_at"].isoformat() if row.get("occurred_at") else None
                ),
                "content": content,
            },
        }

    app.include_router(router)
