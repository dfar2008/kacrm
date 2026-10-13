"""FastAPI routers for the CRM subsystem.

Mounted under ``/crm`` by :func:`hermes_cloud.app.crm.routes.build_router`.
Phase 0 exposes just enough to prove the stack round-trips:

  POST /crm/auth/login       — username + password -> api_key
  GET  /crm/me               — bearer api_key -> user profile
  GET  /crm/users            — admin listing
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from hermes_cloud.app.auth import clear_session_cookie, set_session_cookie
from hermes_cloud.app.bridge.context import RequestContext, get_context
from hermes_cloud.app.bridge.identity import current_user as bridge_current_user
from hermes_cloud.app.config import AppConfig
from hermes.crm.db import DbWrapper, get_db
from hermes.crm.detail_query import DetailQuery
from hermes.crm.edit_query import EditQuery
from hermes.crm.item_delete import ItemDelete
from hermes.crm.list_query import ListQuery
from hermes.crm.models.departments_db import DepartmentsDb
from hermes.crm.models.field_proposals_db import FieldProposalsDb
from hermes.crm.models.module_fields_db import ModuleFieldsDb
from hermes.crm.models.module_views_db import ModuleViewsDb
from hermes.crm.models.modules_db import ModulesDb
from hermes.crm.models.permissions_db import PermissionsDb
from hermes.crm.models.roles_db import RolesDb
from hermes.crm.models.schema_changelog_db import SchemaChangelogDb
from hermes.crm.models.users_db import UsersDb
from hermes.crm.permission import Permission
from hermes.crm.picklist import (
    PicklistError, append_option, join_options, normalize_value, parse_options,
)
from hermes.crm.save import FieldValidationException, Save

from hermes_cloud.app.crm.field_provisioning import provision_field


def _proposer_actor(prop: dict, reviewer: dict) -> dict:
    """审批落地时的归属 actor:优先原申请人(proposer),缺失退回审批管理员。"""
    return {
        "id": int(prop.get("proposer_id") or reviewer["id"]),
        "username": prop.get("proposer_label") or reviewer.get("username"),
    }


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    user_id: int
    username: str
    realname: str | None
    email: str | None
    is_admin: bool
    api_key: str
    plan: str


class MeResponse(BaseModel):
    user_id: int
    username: str
    realname: str | None
    email: str | None
    mobile: str | None
    department_id: int | None
    role_id: int | None
    is_admin: bool
    plan: str


# ---------------------------------------------------------------------------
# Auth dependency — bearer api_key -> user row
#
# Phase 1d routes token resolution through :func:`bridge.identity.current_user`
# so every ``AuthStore`` backend (CRM PG, SA-on-sqlite, InMemory, or the
# static ``config.api_keys`` map) gets the same 401 semantics. We then
# re-fetch the full ``users`` row by id because downstream CRM code
# (Permission / Save / DetailQuery) needs the raw fields — ``role_id``,
# ``department_id``, ``is_admin`` — that the ``UserAccount`` surface
# doesn't carry.
# ---------------------------------------------------------------------------

def _require_user(
    request: Request,
    db: DbWrapper = Depends(get_db),
) -> dict:
    account = bridge_current_user(request)  # raises 401 on miss
    # account.id may be a hex UUID (InMemory / preview) or a decimal
    # integer (CRM PG). CRM routes need an integer PK — reject
    # non-numeric ids with 401 instead of crashing with ValueError.
    try:
        uid = int(account.id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="CRM requires a numeric user id; this account uses a non-numeric id",
        )
    row = UsersDb(db).get_user_by_id(uid)
    if (
        row is None
        or row.get("deleted")
        or row.get("status") != 1
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user inactive",
        )
    return row


def _require_admin(user: dict = Depends(_require_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin only",
        )
    return user


def _resolve_module(db: DbWrapper, module_name: str) -> dict:
    """Look up a module by name or raise 404.

    Module-level twin of the closure used throughout ``build_router`` —
    lifted out so handlers defined at module scope (and their unit tests)
    can share the exact same lookup + 404 contract.
    """
    mod = ModulesDb(db).get_module_by_name(module_name)
    if not mod:
        raise HTTPException(status_code=404, detail="unknown module")
    return mod


def ai_fill_record_endpoint(
    module_name: str,
    record_id: int,
    payload: dict,
    user: dict = Depends(_require_admin),
    db: DbWrapper = Depends(get_db),
) -> dict:
    """手动触发某记录的 AI 字段填值(同步,期待即时回显)。

    Body: ``{fields?: [field_name...], reason?}``。fields 省略=该记录全部
    AI 字段。v1 同步跑满配 agent;前端应限制一次少量字段,避免长阻塞。
    """
    _resolve_module(db, module_name)  # 校验模块存在 + 抛 404
    fields = payload.get("fields") if isinstance(payload, dict) else None
    if fields is not None and not isinstance(fields, list):
        raise HTTPException(status_code=400, detail="fields must be a list")
    from hermes_cloud.app.crm.ai_attributes import fill_record
    out = fill_record(
        module_name=module_name,
        record_id=int(record_id),
        fields=fields,
        trigger="manual",
        actor_id=int(user["id"]),
    )
    return out


def ai_field_draft_config_endpoint(
    payload: dict,
    user: dict = Depends(_require_admin),
    db: DbWrapper = Depends(get_db),
) -> dict:
    """一句话需求 → AI 字段配置(供构建器预填)。Body: {intent, module}。"""
    intent = str((payload or {}).get("intent") or "").strip()
    module = str((payload or {}).get("module") or "").strip()
    if not intent or not module:
        raise HTTPException(status_code=400, detail="intent and module required")
    mod = _resolve_module(db, module)
    field_names = [
        str(f.get("field_name"))
        for f in (ModuleFieldsDb(db).get_module_fields(int(mod["id"])) or [])
    ]
    from hermes_cloud.app.crm.ai_attributes import design_field_config
    cfg = design_field_config(user_id=int(user["id"]), intent=intent,
                              module=module, field_names=field_names)
    if not cfg:
        raise HTTPException(status_code=502, detail="AI 生成失败,请重试或手动填写")
    return {"config": cfg}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def _collect_viewable_user_ids(db: DbWrapper, user_id: int) -> list[int]:
    """Backward-compat shim — Phase 5.1 unified the implementation.

    Delegates to the canonical ``app.scope.viewable_user_ids`` so this
    module's `_build_owner_scope` and the dashboard / auto_drive paths
    share one resolver. The returned shape is preserved (sorted list)
    so existing callers don't change.
    """
    from hermes_cloud.app.scope import viewable_user_ids
    return sorted(viewable_user_ids(db, caller_id=int(user_id), scope="team"))


def _build_owner_scope(
    user: dict, table_name: str, db: DbWrapper
) -> tuple[str, list]:
    """Build a row-level scope for non-admin reads (hierarchical).

    Returns a ``(sql_fragment, params)`` pair suitable for
    ``ListQuery.set_extra_where``. Admins get an empty fragment.
    Non-admins see records whose ``owner_by`` OR ``created_by`` is in
    the set ``{caller}`` ∪ ``{caller's subordinates}`` (resolved via
    department.manager_ids + parent_id descent).

    Charter v2 RBAC: row-level isolation per non-admin user, with a
    manager-can-see-subordinates exemption so a sales lead can audit
    their team's pipeline without being granted blanket access.
    """
    if user.get("is_admin"):
        return ("", [])
    uids = _collect_viewable_user_ids(db, int(user["id"]))
    if not uids:
        # No viewable users at all — degenerate (shouldn't happen since
        # caller is in their own list). Fall back to a never-true clause
        # so the query returns 0 rows instead of every row.
        return ("FALSE", [])
    # Build IN-list with one placeholder per id for both columns.
    placeholders = ", ".join(["%s"] * len(uids))
    sql = (
        f'("{table_name}"."owner_by" IN ({placeholders}) '
        f'OR "{table_name}"."created_by" IN ({placeholders}))'
    )
    return (sql, list(uids) + list(uids))


def require_permission(module_param: str = "module_name", action: str = "index"):
    """FastAPI dependency factory — plan §6.1 RBAC middleware.

    Returns a ``Depends()``-compatible callable that reads the module name
    from the path parameter *module_param*, checks the caller's role
    against ``role_permissions``, and raises 403 on denial. Admins pass
    unconditionally.

    Usage::

        @router.get("/records/{module_name}")
        def list_records(
            module_name: str,
            user: dict = Depends(require_permission("module_name", "index")),
            ...
        ):
    """
    def _dep(
        request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        user = _require_user(request, db)
        module_name = request.path_params.get(module_param, "")
        if not module_name:
            raise HTTPException(status_code=400, detail="module_name missing")
        if not user.get("is_admin"):
            allowed = Permission(db).check_permission_by_module_name(
                int(user["id"]), module_name, action,
            )
            if not allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"no permission to {action} {module_name}",
                )
        return user
    return _dep


# Keys PATCH /crm/modules/{m}/fields/{name} may write. field_name and
# field_type are deliberately absent — renames break every saved view
# and cached query; type changes break existing row data (both need a
# migration, not a PATCH). is_summary is absent: no query consumes it.
PATCH_FIELD_ALLOWED_KEYS = {
    "field_label", "is_hidden", "is_required", "description",
    "is_searchable", "is_readonly", "default_value", "field_config",
}


def validate_reorder(order: Any, visible_field_names: set[str]) -> None:
    """Validate a reorder request body's ``order`` list.

    ``order`` must be exactly the set of non-hidden field names — a
    permutation, no missing names, no unknowns, no duplicates. Hidden
    fields keep their own sort_order and never appear here. Raises
    HTTP 400 on any mismatch.
    """
    if not isinstance(order, list):
        raise HTTPException(
            status_code=400, detail="'order' must be a list")
    if len(order) != len(set(order)):
        raise HTTPException(
            status_code=400, detail="'order' contains duplicates")
    order_set = set(order)
    unknown = order_set - visible_field_names
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown or hidden field(s) in order: "
                   f"{sorted(unknown)}",
        )
    missing = visible_field_names - order_set
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"order is missing non-hidden field(s): "
                   f"{sorted(missing)}",
        )


def formula_expression_of(field_config: dict | None) -> str:
    """Return the formula expression from a field_config, or "" if absent.

    A field_config of None (legacy rows), one without a "formula" key, or
    one whose expression is blank all resolve to "" — i.e. "no formula".
    """
    if not isinstance(field_config, dict):
        return ""
    formula = field_config.get("formula")
    if not isinstance(formula, dict):
        return ""
    expr = formula.get("expression")
    return expr.strip() if isinstance(expr, str) else ""


def build_router() -> APIRouter:
    router = APIRouter(prefix="/crm", tags=["crm"])

    @router.post("/auth/login", response_model=LoginResponse)
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        db: DbWrapper = Depends(get_db),
    ) -> LoginResponse:
        users = UsersDb(db)
        # Plan §4.1 / D4: dual-channel login — accept username OR email
        identifier = body.username.strip()
        row = users.get_user_by_username(identifier)
        if row is None and "@" in identifier:
            row = users.get_user_by_email(identifier)
        if row is None or row.get("deleted") or row.get("status") != 1:
            raise HTTPException(status_code=401, detail="invalid credentials")
        if not users.verify_password(row["id"], body.password):
            raise HTTPException(status_code=401, detail="invalid credentials")
        api_key = row.get("api_key")
        if not api_key:
            raise HTTPException(
                status_code=500,
                detail="user has no api_key provisioned; run seed / admin rotate",
            )
        # Issue the HttpOnly session cookie so the SPA never has to
        # remember the raw api_key client-side.
        cfg: AppConfig = getattr(request.app.state, "config", None) or AppConfig()
        if cfg.session_cookie_secret:
            set_session_cookie(response, cfg.session_cookie_secret, str(row["id"]), api_key)
        return LoginResponse(
            user_id=row["id"],
            username=row["username"],
            realname=row.get("realname"),
            email=row.get("email"),
            is_admin=bool(row.get("is_admin")),
            api_key=api_key,
            plan=row.get("plan") or "free",
        )

    @router.post("/auth/logout")
    def logout(response: Response) -> dict:
        clear_session_cookie(response)
        return {"ok": True}

    @router.get("/me", response_model=MeResponse)
    def me(user: dict = Depends(_require_user)) -> MeResponse:
        return MeResponse(
            user_id=user["id"],
            username=user["username"],
            realname=user.get("realname"),
            email=user.get("email"),
            mobile=user.get("mobile"),
            department_id=user.get("department_id"),
            role_id=user.get("role_id"),
            is_admin=bool(user.get("is_admin")),
            plan=user.get("plan") or "free",
        )

    @router.get("/users")
    def list_users(
        page: int = 1,
        limit: int = 20,
        search: str = "",
        _admin: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        result = UsersDb(db).get_users_list(page=page, limit=limit, search=search)
        # Strip password hash before returning
        for u in result["users"]:
            u.pop("password", None)
        return result

    # ------------------------------------------------------------------
    # RBAC: roles / departments / permissions / me-permissions
    # ------------------------------------------------------------------

    @router.get("/roles")
    def list_roles(
        _user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> list[dict]:
        # Any authenticated user may read the role list — needed for UI
        # dropdowns ("assign to role ..."). Writes still require admin.
        return RolesDb(db).get_all_roles()

    @router.get("/users/picker")
    def users_picker(
        search: str = "",
        limit: int = 50,
        _user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> list[dict]:
        # Lightweight picker for FK (user/ownerby) inline edit: id +
        # display name + dept name, any authenticated user. The full
        # /users endpoint is admin-gated and returns hashes/role ids.
        result = UsersDb(db).get_users_list(page=1, limit=max(1, min(limit, 200)), search=search)
        out: list[dict] = []
        for u in result.get("users", []):
            out.append({
                "id": u.get("id"),
                "name": u.get("realname") or u.get("username") or f"#{u.get('id')}",
                "username": u.get("username"),
                "department_name": u.get("department_name"),
            })
        return out

    @router.get("/departments")
    def list_departments(
        _user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> list[dict]:
        return DepartmentsDb(db).get_all_departments()

    @router.get("/permissions")
    def list_permissions(
        _user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> list[dict]:
        # Permission catalog is a public-to-authenticated-users read so the
        # frontend can render grant matrices without a separate admin bounce.
        return PermissionsDb(db).get_all_permissions()

    @router.get("/me/permissions")
    def my_permissions(
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Return the caller's effective per-module grants.

        Shape::

            {
              "is_admin": bool,
              "role_id": int | null,
              "role_name": str | null,
              "modules": {
                "accounts": ["index", "create", ...],
                "contacts": [...]
              }
            }

        Admins receive every (collection × permission) the DB knows about.
        Non-admins get only rows with ``has_permission = TRUE`` for their role.
        """
        is_admin = bool(user.get("is_admin"))
        role_id = user.get("role_id")

        # Gather grants.
        if is_admin:
            rows = db.fetchall(
                """
                SELECT c.name AS collection_name, p.code AS permission_code
                FROM collections c CROSS JOIN permissions p
                """
            )
        else:
            if role_id is None:
                return {
                    "is_admin": False,
                    "role_id": None,
                    "role_name": None,
                    "modules": {},
                }
            rows = db.fetchall(
                """
                SELECT c.name AS collection_name, p.code AS permission_code
                FROM role_permissions rp
                JOIN collections c  ON c.id = rp.module_id
                JOIN permissions p  ON p.id = rp.permission_id
                WHERE rp.role_id = %s AND rp.has_permission = TRUE
                ORDER BY c.name, p.code
                """,
                (role_id,),
            )

        modules: dict[str, list[str]] = {}
        for r in rows:
            modules.setdefault(r["collection_name"], []).append(
                r["permission_code"]
            )

        role_name = None
        if role_id is not None:
            role_name = RolesDb(db).get_role_name(role_id) or None

        return {
            "is_admin": is_admin,
            "role_id": role_id,
            "role_name": role_name,
            "modules": modules,
        }

    @router.get("/me/permissions/{module_name}/{action}")
    def check_my_permission(
        module_name: str,
        action: str,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Point-check a single (module, action) for the current user.

        Useful for conditional UI rendering — the frontend asks "can I show
        the delete button?" without pulling the whole permissions map.
        """
        allowed = Permission(db).check_permission_by_module_name(
            int(user["id"]), module_name, action
        )
        return {"module": module_name, "action": action, "allowed": allowed}

    # ------------------------------------------------------------------
    # Generic record CRUD — metadata-driven.
    #
    # Every module in the ``collections`` registry (accounts, contacts,
    # leads, ...) is served from a single set of endpoints below. The URL
    # segment is the ``collections.name`` value, and every call funnels
    # through ListQuery / DetailQuery / EditQuery / Save / ItemDelete so
    # we don't need per-module routing boilerplate.
    # ------------------------------------------------------------------

    def _resolve_module(db: DbWrapper, module_name: str) -> dict:
        mod = ModulesDb(db).get_module_by_name(module_name)
        if not mod:
            raise HTTPException(status_code=404, detail="unknown module")
        return mod

    # --- Charter list endpoints (Law 1 — events > state) ------------------
    #
    # The stock list_records path returns everything sorted by
    # ``updated_at DESC`` (a state-level timestamp). Under the charter the
    # only timestamp that matters to a human is "when did I last touch
    # this record" — an event-level stat projected out of ``interactions``.
    # segments_list below groups records into "today / overdue / dormant /
    # new" using the same projection, and list_records re-sorts the page
    # by ``latest_contact_date`` when the caller didn't pin a sort field.

    # ---- segment config (built-in segment tabs, per-module override) -----
    #
    # The five segment *kinds* are fixed; segments_config (migration 0100)
    # lets an admin toggle which are enabled, reorder, relabel, and tune the
    # two thresholds (new.days / dormant.days). NULL → these defaults.
    _SEGMENT_DEFAULTS = [
        {"id": "today",   "enabled": True, "label": "今日焦点", "order": 1},
        {"id": "overdue", "enabled": True, "label": "逾期",     "order": 2},
        {"id": "new",     "enabled": True, "label": "本周新增", "order": 3,
         "params": {"days": 7}},
        {"id": "dormant", "enabled": True, "label": "沉睡",     "order": 4,
         "params": {"days": 30}},
        {"id": "all",     "enabled": True, "label": "全部",     "order": 5},
    ]

    def _default_segments_config() -> list:
        import copy
        return copy.deepcopy(_SEGMENT_DEFAULTS)

    def _load_segments_config(db: DbWrapper, module_name: str) -> list:
        """Resolved segment config for a module — stored override merged
        onto defaults so every known id is always present and thresholds
        always have a value."""
        mod = ModulesDb(db).get_module_by_name(module_name)
        raw = (mod or {}).get("segments_config")
        if isinstance(raw, str):
            try:
                import json as _json
                raw = _json.loads(raw)
            except Exception:
                raw = None
        items = (raw or {}).get("items") if isinstance(raw, dict) else None
        defaults = {d["id"]: d for d in _default_segments_config()}
        if not isinstance(items, list):
            return _default_segments_config()
        by_id = {
            it.get("id"): it for it in items
            if isinstance(it, dict) and it.get("id") in defaults
        }
        merged = []
        for sid, dflt in defaults.items():
            ov = by_id.get(sid) or {}
            item = dict(dflt)
            if "enabled" in ov:
                item["enabled"] = bool(ov["enabled"])
            if ov.get("label"):
                item["label"] = str(ov["label"])
            if isinstance(ov.get("order"), int):
                item["order"] = ov["order"]
            if "params" in dflt:
                days = (ov.get("params") or {}).get("days")
                if isinstance(days, int) and days > 0:
                    item["params"] = {"days": days}
            merged.append(item)
        merged.sort(key=lambda x: x.get("order", 99))
        return merged

    def _segment_thresholds(cfg: list) -> tuple:
        new_days, dormant_days = 7, 30
        for it in cfg:
            if it.get("id") == "new":
                new_days = (it.get("params") or {}).get("days", 7)
            elif it.get("id") == "dormant":
                dormant_days = (it.get("params") or {}).get("days", 30)
        return new_days, dormant_days

    def _apply_charter_sort_and_segment(
        payload: dict, module_name: str, segment: str | None,
        new_days: int = 7, dormant_days: int = 30,
        apply_sort: bool = True,
    ) -> dict:
        from datetime import datetime, timezone, timedelta

        items = payload.get("data") or []

        def _parse(iso: Any) -> Optional[datetime]:
            if not iso:
                return None
            try:
                dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            except Exception:
                return None
            # Normalise naive datetimes (the DB-native created_at /
            # updated_at come back as naive) to the same tz as ``now``
            # so the bucket comparisons don't TypeError.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        now = datetime.now(tz=timezone.utc)
        today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        week_ago = now - timedelta(days=new_days)
        dormant_cut = now - timedelta(days=dormant_days)

        def _bucket_of(r: dict) -> str:
            nxt = _parse(r.get("next_contact_date"))
            latest = _parse(r.get("latest_contact_date"))
            created = _parse(r.get("created_at"))
            if nxt and nxt < now:
                return "overdue"
            if nxt and nxt.date() == now.date():
                return "today"
            if latest and latest >= today_start:
                return "today"
            if created and created >= week_ago:
                return "new"
            if not latest or latest < dormant_cut:
                return "dormant"
            return "other"

        # Tag every row so the client can render a per-row badge if it
        # wants, independent of the current tab filter.
        for r in items:
            r["_segment"] = _bucket_of(r)

        if segment and segment != "all":
            items = [r for r in items if r.get("_segment") == segment]

        # Charter sort: recently-touched records bubble up. We use the
        # MAX(latest_contact_date, created_at) so freshly-created
        # records aren't punished for not yet having a contact event —
        # otherwise "I just added this customer" sinks below long-stale
        # records with an old contact date, which contradicts user
        # expectation. Truly dormant records (old AND never contacted)
        # still go to the bottom.
        def _sort_key(r: dict):
            candidates = [
                t for t in (
                    _parse(r.get("latest_contact_date")),
                    _parse(r.get("created_at")),
                ) if t is not None
            ]
            most_recent = max(candidates) if candidates else None
            ts = most_recent.timestamp() if most_recent else float("-inf")
            return (-ts, -int(r.get("id") or 0))

        # Only re-order when the caller hasn't pinned an explicit sort field.
        # With an explicit ``sort_field`` the SQL ``ORDER BY`` already set the
        # order; here we must preserve it and merely apply the segment filter.
        if apply_sort:
            items.sort(key=_sort_key)
        payload["data"] = items
        payload["segment"] = segment or "all"
        return payload

    def _resolve_sort_field(lq: ListQuery, sort_field: str | None) -> str | None:
        """Return ``sort_field`` only if it is a real, physical column of this
        module's table; otherwise ``None`` (caller falls back to the charter
        default).

        Guards against ``ORDER BY <nonexistent column>`` 500s. We validate
        against ``information_schema`` — the table's ACTUAL columns — not
        ``lq.fields_list``, because that list carries entries with no backing
        column (synthetic ``product_agg_*`` aggregates, and metadata-only /
        drifted fields like a freshly-defined field whose ALTER TABLE hasn't
        run). It also excludes ``id`` (a Python-added row alias for
        ``{table}_id``, not a column) and ``latest_contact_date`` (a
        post-query projection) — so "recent activity" stays the charter
        default sort, never an explicit ORDER BY. Membership in a set of real
        column names is itself injection-safe; ``set_sort`` re-validates the
        identifier as defence in depth.
        """
        if not sort_field:
            return None
        name = str(sort_field).strip()
        if not name or name.startswith("product_agg_"):
            return None
        tn = lq.table_name or ""
        if not tn:
            return None
        try:
            rows = lq._ensure_db().fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND table_schema = current_schema()",
                (tn,),
            )
        except Exception:
            return None
        real_cols = {str(r.get("column_name") or "") for r in (rows or [])}
        return name if name in real_cols else None

    @router.get("/segments/{module_name}")
    def list_segments(
        module_name: str,
        scope: str | None = None,
        owner_id: int | None = None,
        user: dict = Depends(require_permission("module_name", "index")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Event-driven segment counts for the module list sidebar.

        Charter Law 1/5: the list's first impression should be "what does
        Copilot want me to work on today", not a generic grid. We compute
        four buckets from a single pass over the most-recent page of
        records (capped at 200 to stay under a request budget — if a user
        has more than that, pagination is the right answer, not this
        segment API).

        - ``today``   — next_contact_date=today, or touched today
        - ``overdue`` — next_contact_date before today
        - ``dormant`` — no touch in >30d
        - ``new``     — created in the last 7d
        """
        mod = _resolve_module(db, module_name)
        lq = ListQuery(db)
        lq.set_module(int(mod["id"]), None)
        # v1.7 — segment counts must reflect the same scope / specific-user
        # filter the list itself uses, otherwise "全部 27" stays at 27 even
        # when the visible list has been narrowed to one user's records.
        from hermes_cloud.app.scope import viewable_user_ids
        caller_id = int(user["id"])
        is_admin = bool(user.get("is_admin"))
        if owner_id is not None:
            target = int(owner_id)
            if target == caller_id or is_admin:
                allowed = True
            else:
                try:
                    viewable = viewable_user_ids(
                        db, caller_id=caller_id, scope="team",
                    )
                except Exception:
                    viewable = {caller_id}
                allowed = target in viewable
            if allowed:
                lq.set_extra_where("owner_by = %s", [target])
            else:
                lq.set_extra_where("owner_by = %s", [caller_id])
        elif scope == "self":
            # "我的" = 我负责的 OR 我创建的 (see list_records below).
            lq.set_extra_where(
                "(owner_by = %s OR created_by = %s)",
                [caller_id, caller_id],
            )
        elif scope == "team":
            try:
                viewable = sorted(viewable_user_ids(
                    db, caller_id=caller_id, scope="team",
                ))
            except Exception:
                viewable = [caller_id]
            lq.set_extra_where("owner_by = ANY(%s::bigint[])", [viewable])
        elif scope == "org":
            if not is_admin:
                # auto-downgrade — match list_records 'org' admin gate
                try:
                    viewable = sorted(viewable_user_ids(
                        db, caller_id=caller_id, scope="team",
                    ))
                except Exception:
                    viewable = [caller_id]
                lq.set_extra_where("owner_by = ANY(%s::bigint[])", [viewable])
        lq.set_pagination(1, 200)
        payload = lq.execute()
        cfg = _load_segments_config(db, module_name)
        new_days, dormant_days = _segment_thresholds(cfg)
        enriched = _apply_charter_sort_and_segment(
            payload, module_name, None, new_days, dormant_days,
        )
        from collections import Counter
        counts = Counter(r.get("_segment") or "other" for r in enriched["data"])
        total = len(enriched["data"])

        def _count_for(sid: str) -> int:
            return total if sid == "all" else counts.get(sid, 0)

        segments = [
            {"id": it["id"], "label": it["label"], "count": _count_for(it["id"])}
            for it in cfg if it.get("enabled")
        ]
        return {"module": module_name, "segments": segments}

    @router.get("/records/{module_name}")
    def list_records(
        module_name: str,
        page: int = 1,
        limit: int = 20,
        search: str = "",
        view_id: int | None = None,
        sort_field: str | None = None,
        sort_direction: str = "DESC",
        segment: str | None = None,
        scope: str | None = None,  # 5.2.A — 'self' | 'team' | 'org'
        owner_id: int | None = None,  # v1.7 — narrow to specific user
        filters: str | None = None,  # Spec 4 — ad-hoc advanced search (JSON)
        filter_logic: str = "all",   # Spec 4 — 'all' | 'any'
        user: dict = Depends(require_permission("module_name", "index")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        lq = ListQuery(db)
        # NOTE: pass None (not view_id) to set_module. Passing a view_id makes
        # the query restrict its projection to the view's column set and drop
        # the relation/user/department ``*_display`` siblings — which kanban
        # (group-by owner/department) and other views need for labels. We
        # apply the view's *filters* ourselves below, so the column config
        # (module_view_fields, which we don't use) isn't needed here.
        lq.set_module(int(mod["id"]), None)
        # Custom view filters — apply the saved view's filter conditions
        # (resolving dynamic placeholders current/team/date tokens per
        # request). They AND with scope's extra_where in execute().
        _seg_cfg = _load_segments_config(db, module_name)
        _new_days, _dormant_days = _segment_thresholds(_seg_cfg)
        from hermes_cloud.app.crm.view_filters import resolve_dynamic_filters
        # Ad-hoc advanced search (Spec 4) takes precedence over a view's
        # filters; the SPA sends one or the other (mutually exclusive).
        _adhoc = None
        if filters:
            try:
                import json as _json
                parsed = _json.loads(filters)
                if isinstance(parsed, list):
                    _adhoc = parsed
            except Exception:
                _adhoc = None
        if _adhoc:
            _resolved = resolve_dynamic_filters(db, _adhoc, user=user)
            if _resolved:
                lq.set_filters(
                    _resolved, filter_logic if filter_logic in ("all", "any") else "all",
                )
        elif view_id is not None:
            _view = ModuleViewsDb(db).get_view_by_id(int(view_id))
            if _view and _view.get("filters"):
                _resolved = resolve_dynamic_filters(
                    db, _view.get("filters"), user=user,
                )
                if _resolved:
                    lq.set_filters(
                        _resolved, str(_view.get("filter_logic") or "all"),
                    )
        # Phase 5.2.A — explicit scope from SPA segment.
        # v1.4: each scope explicitly applies its filter (no admin
        # short-circuit). Without this, admin's 'team' / 'org' both
        # showed all records identical to 'self'+admin records, so
        # the segment had no visible effect for admin demos.
        from hermes_cloud.app.scope import viewable_user_ids
        from hermes_cloud.app.scope.audit import record_scope_access
        caller_id = int(user["id"])
        is_admin = bool(user.get("is_admin"))

        # v1.7 — specific-user filter. Admin may pick anyone; manager
        # may only pick a subordinate (member of viewable_user_ids
        # under 'team'/'org' scope). Self-pick collapses to 'self'.
        if owner_id is not None:
            target = int(owner_id)
            if target == caller_id:
                lq.set_extra_where("owner_by = %s", [caller_id])
                try:
                    record_scope_access(
                        db, caller_id=caller_id, scope_kind="self",
                        resource_kind="crm_list_records",
                        resource_filter={"module": module_name,
                                         "specific_user_id": target},
                    )
                except Exception:
                    pass
            else:
                if is_admin:
                    allowed = True
                else:
                    try:
                        viewable = viewable_user_ids(
                            db, caller_id=caller_id, scope="team",
                        )
                    except Exception:
                        viewable = {caller_id}
                    allowed = target in viewable
                if not allowed:
                    raise HTTPException(
                        403, "owner_id not in your viewable scope"
                    )
                lq.set_extra_where("owner_by = %s", [target])
                try:
                    record_scope_access(
                        db, caller_id=caller_id, scope_kind="team",
                        resource_kind="crm_list_records",
                        resource_filter={"module": module_name,
                                         "specific_user_id": target},
                    )
                except Exception:
                    pass
            if search:
                lq.set_search(search)
            eff_sort = _resolve_sort_field(lq, sort_field)
            if eff_sort:
                lq.set_sort(eff_sort, sort_direction)
            # Post-pass (segment filter and/or charter sort) is needed when no
            # explicit sort is pinned, OR a segment tab is active (its bucket
            # filter still applies even under an explicit field sort).
            need_post = (not eff_sort) or bool(segment)
            effective_limit = max(limit, 200) if need_post else limit
            lq.set_pagination(page, effective_limit)
            payload = lq.execute()
            if need_post:
                payload = _apply_charter_sort_and_segment(
                    payload, module_name, segment, _new_days, _dormant_days,
                    apply_sort=not eff_sort,
                )
                data = payload.get("data") or []
                payload["total_filtered"] = len(data)
                payload["data"] = data[:limit]
            return payload

        if scope == "self":
            # "我的" = 我负责的 OR 我创建的.
            # 用户决策 (2026-06-17): 建单后负责人可能转交给别人,
            # 但创建者仍要在"我的"里看到自己建的单。覆盖原 v1.5
            # owner-only 语义。team/指定他人成员仍按 owner_by。
            lq.set_extra_where(
                "(owner_by = %s OR created_by = %s)",
                [caller_id, caller_id],
            )
            try:
                record_scope_access(
                    db, caller_id=caller_id, scope_kind="self",
                    resource_kind="crm_list_records",
                    resource_filter={"module": module_name},
                )
            except Exception:
                pass
        elif scope == "team":
            # Caller + subordinates (recursive dept.manager_ids).
            # Non-manager calling 'team' → viewable = {self} → same
            # as 'self' (no error, graceful degrade).
            try:
                viewable = sorted(viewable_user_ids(
                    db, caller_id=caller_id, scope="team",
                ))
            except Exception:
                viewable = [caller_id]
            lq.set_extra_where(
                "owner_by = ANY(%s::bigint[])",
                [viewable],
            )
            if not is_admin:
                try:
                    record_scope_access(
                        db, caller_id=caller_id, scope_kind="team",
                        resource_kind="crm_list_records",
                        resource_filter={"module": module_name},
                    )
                except Exception:
                    pass
        elif scope == "org":
            if not is_admin:
                raise HTTPException(403, "scope='org' is admin-only")
            # No filter — admin sees everything.
            try:
                record_scope_access(
                    db, caller_id=caller_id, scope_kind="org",
                    resource_kind="crm_list_records",
                    resource_filter={"module": module_name},
                )
            except Exception:
                pass
        else:
            # No scope param — keep legacy implicit behavior:
            # admin → empty (all); non-admin → _build_owner_scope.
            scope_sql, scope_params = _build_owner_scope(user, module_name, db)
            # 合并被共享给 caller 的 record_ids — 它们应当出现在默认 list 里,
            # 与 owner-scope 取并集。仅对非 admin 拼装(admin 拿全集,无需合并)。
            if not user.get("is_admin"):
                from hermes_cloud.app.crm.collaborators import shared_record_ids_for_user
                shared_ids = sorted(shared_record_ids_for_user(
                    db, user_id=int(user["id"]), module_id=int(mod["id"]),
                ))
                if shared_ids:
                    # 拼 OR 子句;若 scope_sql 已有,就 (scope) OR (shared);否则只用 shared
                    shared_clause = f"{module_name}_id = ANY(%s::bigint[])"
                    if scope_sql:
                        scope_sql = f"({scope_sql}) OR ({shared_clause})"
                        scope_params = list(scope_params) + [shared_ids]
                    else:
                        scope_sql = shared_clause
                        scope_params = [shared_ids]
            if scope_sql:
                lq.set_extra_where(scope_sql, scope_params)
        if search:
            lq.set_search(search)
        eff_sort = _resolve_sort_field(lq, sort_field)
        if eff_sort:
            lq.set_sort(eff_sort, sort_direction)
        # When the caller doesn't pin a sort field, pull a larger page so
        # the charter post-sort (by latest_contact_date) has enough to
        # work with; we trim back to `limit` after sorting+filtering. A
        # segment tab also needs the larger page so its in-Python bucket
        # filter has rows to work with — even under an explicit field sort.
        need_post = (not eff_sort) or bool(segment)
        effective_limit = max(limit, 200) if need_post else limit
        lq.set_pagination(page, effective_limit)
        payload = lq.execute()
        if need_post:
            payload = _apply_charter_sort_and_segment(
                payload, module_name, segment, _new_days, _dormant_days,
                apply_sort=not eff_sort,
            )
            data = payload.get("data") or []
            payload["total_filtered"] = len(data)
            payload["data"] = data[:limit]
        return payload

    @router.get("/records/{module_name}/edit")
    def edit_form_new(
        module_name: str,
        user: dict = Depends(require_permission("module_name", "create")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        return (
            EditQuery(db)
            .set_module(int(mod["id"]))
            .set_record_id(0)
            .execute(current_user_id=int(user["id"]))
        )

    @router.get("/records/{module_name}/{record_id}/edit")
    def edit_form_existing(
        module_name: str,
        record_id: int,
        user: dict = Depends(require_permission("module_name", "update")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        try:
            return (
                EditQuery(db)
                .set_module(int(mod["id"]))
                .set_record_id(record_id)
                .execute(current_user_id=int(user["id"]))
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get("/records/{module_name}/{record_id}")
    def detail_record(
        module_name: str,
        record_id: int,
        user: dict = Depends(require_permission("module_name", "detail")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        # Row-level enforcement on detail: a non-admin must own/create
        # the record OR be the manager (direct or recursive) of the
        # owner/creator. Without this, a user with module-level
        # permission can deep-link to /crm/records/<module>/<id> and
        # bypass the list scope.
        if not user.get("is_admin"):
            row = db.fetchone(
                f'SELECT owner_by, created_by FROM "{module_name}" '
                f'WHERE "{module_name}_id" = %s AND (deleted = FALSE OR deleted IS NULL)',
                (record_id,),
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Record not found")
            viewable = set(_collect_viewable_user_ids(db, int(user["id"])))
            owner_visible = (
                row.get("owner_by") in viewable
                or row.get("created_by") in viewable
            )
            # Collaborator path: 即便 owner/层级不通,被显式共享也能看
            if not owner_visible:
                from hermes_cloud.app.crm.collaborators import shared_record_ids_for_user
                shared = shared_record_ids_for_user(
                    db, user_id=int(user["id"]), module_id=int(mod["id"]),
                )
                if record_id not in shared:
                    raise HTTPException(status_code=404, detail="Record not found")
        try:
            return (
                DetailQuery(db)
                .set_module(int(mod["id"]))
                .set_record_id(record_id)
                .set_user(int(user["id"]), bool(user.get("is_admin")))
                .execute()
            )
        except ValueError as e:
            # A missing record is a 404 — including the soft-deleted case,
            # where DetailQuery filters out ``deleted = TRUE`` rows. Callers
            # that want module field metadata without a record must use
            # ``GET /crm/modules/{module_name}/fields`` instead.
            raise HTTPException(status_code=404, detail=str(e))

    @router.get("/modules/{module_name}/records/{record_id}/attachments")
    def record_attachments(
        module_name: str,
        record_id: int,
        user: dict = Depends(require_permission("module_name", "detail")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Source files (images/PDFs) uploaded in the chat that created or
        updated this record. Same ``detail`` permission gate as the record
        itself — you can only see a record's attachments if you can see it."""
        from hermes_cloud.app.attachments.repo import list_for_record
        items = list_for_record(db, module=module_name, record_id=int(record_id))
        return {"attachments": items}

    @router.get("/records/{module_name}/{record_id}/related")
    def related_records(
        module_name: str,
        record_id: int,
        user: dict = Depends(require_permission("module_name", "detail")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Return related records for a charter v2 §定律 1 narrative
        panel. Supports four parent module types:

          - ``accounts``   → contacts / potentials / quotes /
                             salesorders / tasks / receivables
                             (everyone with an ``accounts_id`` FK)
          - ``contacts``   → potentials / quotes / salesorders /
                             tasks / receivables
                             (everyone with a ``contacts_id`` FK)
          - ``potentials`` → quotes / salesorders + tasks via the
                             ``related_module`` polymorphic column
          - ``leads``      → tasks only, via ``related_module=leads``

        Empty groups aren't returned — keeps the response payload
        small and the SPA renders only what's actually there.
        Closed/lost rows are NOT filtered server-side (the SPA's
        RelatedEntities does that client-side so the same data can
        feed an admin "show all" view later).
        """
        # Honor the same row-level scope as list_records / detail_record
        # so a non-admin can't peek at sibling records they don't own.
        viewable = (
            None if user.get("is_admin")
            else set(_collect_viewable_user_ids(db, int(user["id"])))
        )

        groups: dict[str, list[dict]] = {}

        def _collect_by_fk(
            table: str, name_col: str, fk_col: str, extra_cols: list[str],
        ) -> list[dict]:
            """Children that point at the parent via a typed FK column
            (``accounts_id`` / ``contacts_id`` / ``potentials_id``)."""
            cols = ["id", "name"] + extra_cols
            select_parts = [
                f'"{table}"."{table}_id" AS id',
                f'"{table}"."{name_col}" AS name',
            ]
            for c in extra_cols:
                select_parts.append(f'"{table}"."{c}" AS "{c}"')
            where = [
                f'"{table}"."{fk_col}" = %s',
                f'("{table}"."deleted" = FALSE OR "{table}"."deleted" IS NULL)',
            ]
            params: list = [int(record_id)]
            if viewable is not None and viewable:
                placeholders = ", ".join(["%s"] * len(viewable))
                where.append(
                    f'("{table}"."owner_by" IN ({placeholders}) '
                    f'OR "{table}"."created_by" IN ({placeholders}))'
                )
                params.extend(list(viewable))
                params.extend(list(viewable))
            sql = (
                f"SELECT {', '.join(select_parts)} "
                f'FROM "{table}" '
                f"WHERE {' AND '.join(where)} "
                f'ORDER BY "{table}"."updated_at" DESC '
                f"LIMIT 8"
            )
            rows = db.fetchall(sql, tuple(params)) or []
            return [{k: r.get(k) for k in cols} for r in rows]

        def _collect_polymorphic_tasks() -> list[dict]:
            """Tasks attached via the polymorphic
            ``related_module`` / ``related_record_id`` pair (covers
            potentials, leads, and any future module without a typed
            tasks FK)."""
            where = [
                '"tasks"."related_module" = %s',
                '"tasks"."related_record_id" = %s',
                '("tasks"."deleted" = FALSE OR "tasks"."deleted" IS NULL)',
            ]
            params: list = [module_name, int(record_id)]
            if viewable is not None and viewable:
                placeholders = ", ".join(["%s"] * len(viewable))
                where.append(
                    f'("tasks"."owner_by" IN ({placeholders}) '
                    f'OR "tasks"."created_by" IN ({placeholders}))'
                )
                params.extend(list(viewable))
                params.extend(list(viewable))
            sql = (
                'SELECT "tasks"."tasks_id" AS id, '
                '       "tasks"."tasks_name" AS name, '
                '       "tasks"."status" AS status, '
                '       "tasks"."due_at" AS due_at, '
                '       "tasks"."priority" AS priority '
                'FROM "tasks" '
                f"WHERE {' AND '.join(where)} "
                'ORDER BY "tasks"."updated_at" DESC '
                'LIMIT 8'
            )
            rows = db.fetchall(sql, tuple(params)) or []
            return [
                {k: r.get(k) for k in ("id", "name", "status", "due_at", "priority")}
                for r in rows
            ]

        if module_name == "accounts":
            groups["contacts"]    = _collect_by_fk("contacts",    "contacts_name",    "accounts_id", ["mobile", "email"])
            groups["potentials"]  = _collect_by_fk("potentials",  "potentials_name",  "accounts_id", ["milestone", "amount"])
            groups["quotes"]      = _collect_by_fk("quotes",      "quotes_name",      "accounts_id", ["status"])
            groups["salesorders"] = _collect_by_fk("salesorders", "salesorders_name", "accounts_id", ["status"])
            groups["tasks"]       = _collect_by_fk("tasks",       "tasks_name",       "accounts_id", ["status", "due_at", "priority"])
            groups["receivables"] = _collect_by_fk("receivables", "receivables_name", "accounts_id", [])
        elif module_name == "contacts":
            groups["potentials"]  = _collect_by_fk("potentials",  "potentials_name",  "contacts_id", ["milestone", "amount"])
            groups["quotes"]      = _collect_by_fk("quotes",      "quotes_name",      "contacts_id", ["status"])
            groups["salesorders"] = _collect_by_fk("salesorders", "salesorders_name", "contacts_id", ["status"])
            groups["tasks"]       = _collect_by_fk("tasks",       "tasks_name",       "contacts_id", ["status", "due_at", "priority"])
            groups["receivables"] = _collect_by_fk("receivables", "receivables_name", "contacts_id", [])
        elif module_name == "potentials":
            groups["quotes"]      = _collect_by_fk("quotes",      "quotes_name",      "potentials_id", ["status"])
            groups["salesorders"] = _collect_by_fk("salesorders", "salesorders_name", "potentials_id", ["status"])
            groups["tasks"]       = _collect_polymorphic_tasks()
        elif module_name == "leads":
            groups["tasks"]       = _collect_polymorphic_tasks()

        # 元数据驱动(P4 §6.5):任意顶层模块(含自定义)只要有指向本模块的
        # ``<module_name>_id`` 关系列,就自动作为关联组列出——补硬编码未覆盖的
        # 自定义关系。已被上面硬编码覆盖的组不重复。关联发现是 advisory,
        # 失败不阻断详情。
        try:
            fk_col = f"{module_name}_id"
            others = db.fetchall(
                "SELECT name FROM collections "
                "WHERE parent_module_id IS NULL AND name <> %s", (module_name,)
            ) or []
            for o in others:
                oname = o.get("name")
                if not oname or oname in groups:
                    continue
                cols = {
                    r["column_name"] for r in (db.fetchall(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s", (oname,)
                    ) or [])
                }
                if fk_col not in cols or f"{oname}_name" not in cols:
                    continue
                rows = _collect_by_fk(oname, f"{oname}_name", fk_col, [])
                if rows:
                    groups[oname] = rows
        except Exception:
            logger.exception("related_records metadata discovery failed")

        # Drop empty groups so the payload stays compact and the SPA
        # never renders a "0 contacts" placeholder.
        groups = {k: v for k, v in groups.items() if v}

        # 组标题:供前端给自定义模块组显示中文名(内置组前端仍用 i18n key)。
        # 取 collections.title;失败不阻断。
        group_titles: dict[str, str] = {}
        if groups:
            try:
                rows = db.fetchall(
                    "SELECT name, title FROM collections WHERE name = ANY(%s)",
                    (list(groups.keys()),)
                ) or []
                group_titles = {r["name"]: r["title"] for r in rows if r.get("title")}
            except Exception:
                pass
        return {"groups": groups, "group_titles": group_titles}

    @router.post("/records/{module_name}")
    def create_record(
        module_name: str,
        data: dict,
        user: dict = Depends(require_permission("module_name", "create")),
        ctx: RequestContext = Depends(get_context),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        try:
            new_id = (
                Save(db)
                .set_module(int(mod["id"]))
                .set_user(int(user["id"]), bool(user.get("is_admin")))
                .set_source(ctx.source)
                .insert(data)
            )
        except FieldValidationException as e:
            raise HTTPException(
                status_code=400,
                detail={"field": e.field_name, "message": e.message},
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"success": True, "id": new_id}

    @router.put("/records/{module_name}/{record_id}")
    def update_record(
        module_name: str,
        record_id: int,
        data: dict,
        user: dict = Depends(require_permission("module_name", "update")),
        ctx: RequestContext = Depends(get_context),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        try:
            save = (
                Save(db)
                .set_module(int(mod["id"]))
                .set_user(int(user["id"]), bool(user.get("is_admin")))
                .set_source(ctx.source)
                .set_record_id(record_id)
            )
            # 双达语义:collaborator 且 can_edit=True → 跳过行级 owner 检查。
            # 模块级 .update 权限已由 require_permission Depends 验证过。
            if not user.get("is_admin"):
                from hermes_cloud.app.crm.collaborators import user_can_edit_record
                if user_can_edit_record(
                    db,
                    user_id=int(user["id"]),
                    module_id=int(mod["id"]),
                    record_id=record_id,
                ):
                    save.set_allow_via_collaborator(True)
            save.update(data)
        except FieldValidationException as e:
            raise HTTPException(
                status_code=400,
                detail={"field": e.field_name, "message": e.message},
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"success": True, "id": record_id}

    @router.post("/records/{module_name}/{record_id}/undo")
    def undo_last_update(
        module_name: str,
        record_id: int,
        user: dict = Depends(require_permission("module_name", "update")),
        ctx: RequestContext = Depends(get_context),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Charter Law 6 — 可撤销 as an architectural primitive.

        Reverses the most recent UPDATE on this record by reading the
        ``changelog`` table (which stores old/new per field) and writing
        ``old_value`` back through the same :class:`Save` path. The
        reversal emits its own Interaction Log row with ``source='undo'``
        so the audit trail stays append-only — we never rewrite history.

        Policy (kept tight for safety):
          * window: 120 seconds — anything older requires a deliberate edit
          * actor:  only the user who performed the original update
          * scope:  only fields whose most-recent change is within window
        """
        mod = _resolve_module(db, module_name)
        module_id = int(mod["id"])
        user_id = int(user["id"])

        # Find the most recent changelog batch for this record within the
        # undo window authored by the current user. We group by a 2-second
        # bucket because a single Save emits one changelog row per field
        # all with effectively-the-same timestamp.
        rows = db.fetchall(
            """
            SELECT field_name, old_value, new_value, updated_at, updated_by
              FROM changelog
             WHERE module_id = %s
               AND data_id = %s
               AND updated_by = %s
               AND updated_at >= now() - interval '120 seconds'
             ORDER BY updated_at DESC, tracking_id DESC
            """,
            (module_id, record_id, user_id),
        )
        if not rows:
            raise HTTPException(
                status_code=400,
                detail={"error": "no_undo_target",
                        "message": "No recent update to undo (120s window, same actor)."},
            )

        # Keep only the top batch — rows within 2s of the newest entry.
        newest = rows[0]["updated_at"]
        batch = [r for r in rows if (newest - r["updated_at"]).total_seconds() <= 2]
        reverse_payload = {r["field_name"]: r["old_value"] for r in batch}

        try:
            save = (
                Save(db)
                .set_module(module_id)
                .set_user(user_id, bool(user.get("is_admin")))
                .set_source("undo")
                .set_record_id(record_id)
            )
            # 双达语义:collaborator 且 can_edit=True → 跳过行级 owner 检查。
            # 模块级 .update 权限已由 require_permission Depends 验证过。
            if not user.get("is_admin"):
                from hermes_cloud.app.crm.collaborators import user_can_edit_record
                if user_can_edit_record(
                    db,
                    user_id=user_id,
                    module_id=module_id,
                    record_id=record_id,
                ):
                    save.set_allow_via_collaborator(True)
            save.update(reverse_payload)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except (ValueError, FieldValidationException) as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "success": True,
            "reverted_fields": [r["field_name"] for r in batch],
            "record_id": record_id,
        }

    # ------------------------------------------------------------------
    # Module listing — used by the unified frontend to build navigation.
    # ------------------------------------------------------------------

    @router.post("/modules")
    def create_module(
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 新建顶层自定义模块。

        body: ``{name, title, title_en?, initial_fields?, reason?}``
        建表+注册 collections+灌系统/初始字段+默认权限+changelog,原子。
        """
        from hermes_cloud.app.crm.module_provisioning import provision_module
        res = provision_module(db, payload, user, payload.get("reason"))
        return {"ok": True, **res}

    @router.patch("/modules/{module_name}")
    def patch_module(
        module_name: str,
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 改模块显示名(中/英),不动物理结构。"""
        from hermes_cloud.app.crm.module_provisioning import rename_module
        res = rename_module(
            db, module_name, payload.get("title"), payload.get("title_en"),
            user, payload.get("reason"))
        return {"ok": True, **res}

    @router.post("/modules/{parent_name}/details")
    def create_subdetail(
        parent_name: str,
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 给模块挂子明细(product/generic)。

        body: ``{name, title, title_en?, detail_kind?, initial_fields?, reason?}``
        """
        from hermes_cloud.app.crm.module_provisioning import provision_subdetail
        res = provision_subdetail(db, parent_name, payload, user,
                                  payload.get("reason"))
        return {"ok": True, **res}

    @router.delete("/modules/{module_name}")
    def delete_module(
        module_name: str,
        archive: bool = True,
        reason: Optional[str] = None,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 安全删除一个顶层自定义模块(含其子明细)。

        删前做依赖检查(核心模块保护 / 子明细不可单删 / 入向 relation /
        写回规则);``archive=true``(默认)会在删前把整模块快照进 module_archive,
        可经 restore 还原。事务原子。
        """
        from hermes_cloud.app.crm.module_provisioning import deprovision_module
        res = deprovision_module(db, module_name, user, reason, archive=archive)
        return {"ok": True, **res}

    @router.get("/modules/archives")
    def list_module_archives(
        _user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 列已归档(已删)的模块,供还原。不含 payload(可能很大)。"""
        rows = db.fetchall(
            "SELECT id, module_name, title, title_en, archived_at, "
            "actor_label, reason, restored_at FROM module_archive "
            "ORDER BY id DESC")
        return {"archives": list(rows or [])}

    @router.post("/modules/archives/{archive_id}/restore")
    def restore_module_archive(
        archive_id: int,
        payload: dict | None = None,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 从归档还原一个已删模块(名字若被重新占用则拒绝)。"""
        from hermes_cloud.app.crm.module_provisioning import restore_module
        reason = (payload or {}).get("reason")
        res = restore_module(db, archive_id, user, reason)
        return {"ok": True, **res}

    @router.get("/modules/{module_name}/subdetails")
    def list_subdetails(
        module_name: str,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """列出某模块的子明细 + 各自字段(供前端渲染明细行编辑器)。

        返回 ``{subdetails: [{name, title, detail_kind, fields:[...]}]}``。
        子明细继承父权限,故此处只要能看父模块即可读。
        """
        mod = _resolve_module(db, module_name)
        kids = db.fetchall(
            "SELECT id, name, title, title_en, detail_kind FROM collections "
            "WHERE parent_module_id = %s ORDER BY id", (int(mod["id"]),)
        ) or []
        mf = ModuleFieldsDb(db)
        out = []
        for k in kids:
            fields = mf.get_module_fields(int(k["id"])) or []
            # 只暴露可录入的业务字段(隐藏系统 id/父键/审计列)
            slim = [
                {"field_name": f.get("field_name"),
                 "field_label": f.get("field_label"),
                 "field_type": f.get("field_type"),
                 "options": f.get("options"),
                 "is_readonly": f.get("is_readonly"),
                 # 行内公式:把表达式带给前端,编辑器只读显示 + 实时预览
                 "field_config": f.get("field_config")}
                for f in fields
                if f.get("field_name") not in (
                    f"{k['name']}_id", f"{module_name}_id",
                    "created_at", "updated_at", "deleted")
                and not f.get("is_hidden")
            ]
            out.append({
                "name": k["name"], "title": k.get("title"),
                "title_en": k.get("title_en"),
                "detail_kind": k.get("detail_kind"), "fields": slim,
            })
        return {"module_name": module_name, "subdetails": out}

    @router.get("/modules/{module_name}/writeback-rules")
    def list_writeback_rules(
        module_name: str,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 列出以该模块为源(source)的汇总(writeback)规则。"""
        from hermes.crm.field_writeback import FieldWriteback
        mod = _resolve_module(db, module_name)
        rules = FieldWriteback(db).list_rules(int(mod["id"]))
        return {"module_name": module_name, "rules": rules}

    @router.post("/modules/{module_name}/writeback-rules")
    def create_writeback_rule(
        module_name: str,
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 建一条汇总规则 —— 把源模块某字段按 calc 聚合到目标模块字段。

        body 支持两种:
        - 名称版(前端友好):``{source_field, target_module, target_field, calc_method}``
        - id 版:``{source_field_id, target_module_id, target_field_id, calc_method}``
        source_module 取 path 的 module。
        """
        from hermes.crm.field_writeback import FieldWriteback
        mod = _resolve_module(db, module_name)
        data = dict(payload)
        data["source_module_id"] = int(mod["id"])

        # 名称 → id 解析(前端只传名称即可)
        def _field_id(module_id: int, field_name: str) -> Optional[int]:
            r = db.fetchone(
                "SELECT id FROM module_fields WHERE module_id = %s "
                "AND field_name = %s", (int(module_id), field_name))
            return int(r["id"]) if r else None

        if "source_field" in data and "source_field_id" not in data:
            fid = _field_id(mod["id"], str(data.get("source_field")))
            if fid is None:
                raise HTTPException(status_code=400,
                                    detail=f"source field not found: {data.get('source_field')!r}")
            data["source_field_id"] = fid
        if "target_module" in data and "target_module_id" not in data:
            tmod = _resolve_module(db, str(data.get("target_module")))
            data["target_module_id"] = int(tmod["id"])
        if "target_field" in data and "target_field_id" not in data:
            fid = _field_id(data.get("target_module_id"), str(data.get("target_field")))
            if fid is None:
                raise HTTPException(status_code=400,
                                    detail=f"target field not found: {data.get('target_field')!r}")
            data["target_field_id"] = fid

        res = FieldWriteback(db, user_id=int(user["id"])).create_rule(data)
        if not res.get("success"):
            raise HTTPException(status_code=400,
                                detail=res.get("error") or "create failed")
        # 汇总目标字段是系统计算值,SPA 手填表单设为只读(防用户手改导致与
        # 明细之和不一致;writeback 引擎/agent 不受 is_readonly 影响仍可写)。
        tfid = data.get("target_field_id")
        if tfid:
            db.execute("UPDATE module_fields SET is_readonly = TRUE WHERE id = %s",
                       (int(tfid),))
        SchemaChangelogDb(db).insert_entry(
            actor_type="user", actor_id=int(user["id"]),
            actor_label=user.get("username"),
            module_id=int(mod["id"]), module_name=module_name,
            field_id=None, field_name=None, op="add_writeback",
            before=None, after={"rule_id": res["id"],
                                "calc": data.get("calc_method")},
            reason=payload.get("reason"))
        return {"ok": True, **res}

    @router.delete("/modules/{module_name}/writeback-rules/{rule_id}")
    def delete_writeback_rule(
        module_name: str,
        rule_id: int,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin: 删除一条汇总规则。"""
        from hermes.crm.field_writeback import FieldWriteback
        mod = _resolve_module(db, module_name)
        # 删前取目标字段,删后若无其它规则指向它则恢复其可编辑(取消只读)。
        rule = db.fetchone(
            "SELECT target_field_id FROM field_writeback WHERE id = %s", (int(rule_id),))
        FieldWriteback(db, user_id=int(user["id"])).delete_rule(int(rule_id))
        tfid = (rule or {}).get("target_field_id")
        if tfid:
            remaining = db.fetchone(
                "SELECT COUNT(*) AS n FROM field_writeback WHERE target_field_id = %s",
                (int(tfid),))
            if int((remaining or {}).get("n") or 0) == 0:
                db.execute(
                    "UPDATE module_fields SET is_readonly = FALSE WHERE id = %s",
                    (int(tfid),))
        SchemaChangelogDb(db).insert_entry(
            actor_type="user", actor_id=int(user["id"]),
            actor_label=user.get("username"),
            module_id=int(mod["id"]), module_name=module_name,
            field_id=None, field_name=None, op="delete_writeback",
            before={"rule_id": rule_id}, after=None,
            reason=None)
        return {"ok": True}

    @router.get("/modules")
    def list_modules(
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Return all CRM modules the current user may access.

        Each item carries ``name``, ``title``, and a boolean ``has_access``
        so the frontend can render greyed-out entries for modules the user
        cannot view.
        """
        all_mods = ModulesDb(db).get_all_modules() or []
        # 子明细(parent_module_id 非 NULL)只内联在父记录,不进主菜单。
        all_mods = [m for m in all_mods if not m.get("parent_module_id")]

        # Build the user's permission map once.
        if user.get("is_admin"):
            allowed = {m["name"] for m in all_mods}
        else:
            perms = db.fetchall(
                """
                SELECT c.name
                FROM role_permissions rp
                JOIN collections c  ON c.id = rp.module_id
                JOIN permissions p  ON p.id = rp.permission_id
                WHERE rp.role_id = %s AND rp.has_permission = TRUE
                  AND p.code = 'index'
                """,
                (user.get("role_id"),),
            ) if user.get("role_id") else []
            allowed = {r["name"] for r in perms}

        # Exclude internal/admin-only modules from the CRM nav. Anything
        # prefixed with ``admin_`` or ``skill.`` belongs to the platform
        # shell, not the CRM record model.
        _INTERNAL_EXACT = {
            "users", "roles", "departments",
            "agents", "chat", "approvals", "analytics",
            # ``mail`` and ``touchpoints`` were registered in collections
            # but never got a real schema / list route. Per charter §168
            # (不暴露内部实现给用户) they shouldn't be module rows at all:
            # emails surface as ``interactions.kind='email'`` and
            # "touchpoint" is an internal write-side term, not a module
            # the user navigates to. Hide from nav until / unless we
            # actually build an interaction-backed view module.
            "mail", "touchpoints",
        }

        # CRM-standard Chinese labels. DB `label` is English for seeded
        # modules; fall back to name otherwise.
        _CN_LABEL = {
            "accounts": "客户", "contacts": "联系人", "leads": "线索",
            "potentials": "商机", "products": "产品",
            "quotes": "报价", "salesorders": "销售订单",
            "tasks": "任务", "receivables": "应收", "payments": "收款",
        }

        result = []
        for m in all_mods:
            nm = m["name"]
            if nm in _INTERNAL_EXACT:
                continue
            if nm.startswith("admin_") or nm.startswith("skill."):
                continue
            label = _CN_LABEL.get(nm) or m.get("label") or m.get("title") or nm
            # ``headline_fields`` is the admin-curated chip-row for this
            # module (migration 0020). NULL means "frontend default" — we
            # pass it through as-is so the client can fall back.
            raw_hf = m.get("headline_fields")
            if isinstance(raw_hf, str):
                # Some psycopg drivers return JSONB as str; decode defensively.
                try:
                    import json as _json
                    raw_hf = _json.loads(raw_hf)
                except Exception:
                    raw_hf = None
            # ``list_fields`` (migration 0099) is the admin-curated two-row
            # field layout for the record *list* items. Shape:
            # ``{"row1": [...], "row2": [...]}``; NULL → frontend default.
            raw_lf = m.get("list_fields")
            if isinstance(raw_lf, str):
                try:
                    import json as _json
                    raw_lf = _json.loads(raw_lf)
                except Exception:
                    raw_lf = None
            result.append({
                "name": nm,
                "label": label,
                "has_access": nm in allowed,
                "headline_fields": raw_hf if isinstance(raw_hf, list) else None,
                "list_fields": raw_lf if isinstance(raw_lf, dict) else None,
            })
        # Frontend expects {data: [...]} envelope to match other CRM endpoints.
        return {"data": result}

    @router.put("/modules/{module_name}/headline")
    def update_module_headline(
        module_name: str,
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: overwrite the org-default chip-row pick list.

        Body: ``{"fields": ["field_a", "field_b", ...]}`` or
        ``{"fields": null}`` to clear the override and fall back to the
        frontend default. Field count is uncapped (per-user personal
        overrides live in the browser; this is the org default).

        Validation:
          * caller must be admin
          * module must exist
          * each entry must be a visible (non-hidden) ``module_fields``
            row on that module
        """
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        mod = _resolve_module(db, module_name)

        raw = payload.get("fields")
        if raw is None:
            ModulesDb(db).set_headline_fields(module_name, None)
            return {"success": True, "fields": None}

        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="'fields' must be a list")
        names = [str(x).strip() for x in raw if str(x).strip()]
        if not names:
            ModulesDb(db).set_headline_fields(module_name, None)
            return {"success": True, "fields": None}

        # Validate every name against the module's visible fields.
        from hermes.crm.models.module_fields_db import ModuleFieldsDb
        visible = ModuleFieldsDb(db).get_visible_fields(int(mod["id"])) or []
        allowed_names = {
            str(f.get("field_name") or "").strip() for f in visible
            if f.get("field_name")
        }
        bad = [n for n in names if n not in allowed_names]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown / hidden field(s): {', '.join(bad)}",
            )
        # Dedup preserving order.
        seen: set[str] = set()
        uniq: list[str] = []
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        ModulesDb(db).set_headline_fields(module_name, uniq)
        return {"success": True, "fields": uniq}

    @router.put("/modules/{module_name}/list-fields")
    def update_module_list_fields(
        module_name: str,
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: overwrite the two-row list-item field layout.

        Body: ``{"fields": {"row1": [...], "row2": [...]}}`` or
        ``{"fields": null}`` to clear the org default (frontend default
        takes over). Unlike headline, field count is *not* capped.

        Validation:
          * caller must be admin
          * module must exist
          * each entry must be a visible (non-hidden) ``module_fields`` row
            on that module (the module's primary name field is allowed —
            unlike headline we do *not* exclude it, since row1 holds it)
          * row1 / row2 must each be lists; each is de-duped preserving order
        """
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        mod = _resolve_module(db, module_name)

        raw = payload.get("fields")
        if raw is None:
            db.execute(
                "UPDATE collections SET list_fields = NULL WHERE name = %s",
                (module_name,),
            )
            return {"success": True, "fields": None}

        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=400, detail="'fields' must be an object",
            )

        from hermes.crm.models.module_fields_db import ModuleFieldsDb
        visible = ModuleFieldsDb(db).get_visible_fields(int(mod["id"])) or []
        allowed_names = {
            str(f.get("field_name") or "").strip() for f in visible
            if f.get("field_name")
        }

        def _clean_row(key: str) -> list[str]:
            row = raw.get(key, [])
            if not isinstance(row, list):
                raise HTTPException(
                    status_code=400, detail=f"'{key}' must be a list",
                )
            names = [str(x).strip() for x in row if str(x).strip()]
            bad = [n for n in names if n not in allowed_names]
            if bad:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown / hidden field(s): {', '.join(bad)}",
                )
            seen: set[str] = set()
            uniq: list[str] = []
            for n in names:
                if n in seen:
                    continue
                seen.add(n)
                uniq.append(n)
            return uniq

        cleaned = {"row1": _clean_row("row1"), "row2": _clean_row("row2")}
        if not cleaned["row1"] and not cleaned["row2"]:
            db.execute(
                "UPDATE collections SET list_fields = NULL WHERE name = %s",
                (module_name,),
            )
            return {"success": True, "fields": None}
        import json as _json
        db.execute(
            "UPDATE collections SET list_fields = %s::jsonb WHERE name = %s",
            (_json.dumps(cleaned), module_name),
        )
        return {"success": True, "fields": cleaned}

    # ------------------------------------------------------------------
    # Segment config — built-in segment tabs (enable/order/label/threshold)
    # ------------------------------------------------------------------

    @router.get("/modules/{module_name}/segments-config")
    def get_segments_config(
        module_name: str,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Resolved segment config (override merged onto defaults)."""
        _resolve_module(db, module_name)
        return {"module": module_name, "items": _load_segments_config(db, module_name)}

    @router.put("/modules/{module_name}/segments-config")
    def update_segments_config(
        module_name: str,
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: override the built-in segment tabs for a module.

        Body: ``{"config": {"items": [{"id","enabled","label","order",
        "params":{"days"}}, ...]}}`` or ``{"config": null}`` to reset.
        Segment ids are fixed (today/overdue/new/dormant/all).
        """
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        _resolve_module(db, module_name)
        cfg = payload.get("config")
        if cfg is None:
            db.execute(
                "UPDATE collections SET segments_config = NULL WHERE name = %s",
                (module_name,),
            )
            return {"success": True, "items": _load_segments_config(db, module_name)}
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=400, detail="'config' must be an object")
        items = cfg.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="'items' must be a list")
        valid_ids = {d["id"] for d in _default_segments_config()}
        threshold_ids = {"new", "dormant"}
        clean_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            sid = it.get("id")
            if sid not in valid_ids:
                raise HTTPException(status_code=400, detail=f"Unknown segment id: {sid}")
            entry: dict = {"id": sid, "enabled": bool(it.get("enabled", True))}
            if it.get("label"):
                entry["label"] = str(it["label"])
            if isinstance(it.get("order"), int):
                entry["order"] = it["order"]
            if sid in threshold_ids:
                days = (it.get("params") or {}).get("days")
                if days is not None:
                    if not isinstance(days, int) or days <= 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{sid}.params.days must be a positive integer",
                        )
                    entry["params"] = {"days": days}
            clean_items.append(entry)
        import json as _json
        db.execute(
            "UPDATE collections SET segments_config = %s::jsonb WHERE name = %s",
            (_json.dumps({"items": clean_items}), module_name),
        )
        return {"success": True, "items": _load_segments_config(db, module_name)}

    # ------------------------------------------------------------------
    # Custom views — saved filter sets (module_views)
    # ------------------------------------------------------------------

    _VIEW_OPERATORS = {
        "eq", "neq", "gt", "gte", "lt", "lte", "in", "like", "contains",
        "not_contains", "starts_with", "ends_with", "is_empty", "is_not_empty",
    }
    _VIEW_DYNAMIC_VALUES = {"current", "team"}

    def _validate_view_body(db: DbWrapper, mod: dict, body: dict) -> dict:
        """Validate + normalize a view create/update payload. Returns the
        data dict ready for ModuleViewsDb."""
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="'name' is required")
        filters = body.get("filters") or []
        if not isinstance(filters, list):
            raise HTTPException(status_code=400, detail="'filters' must be a list")
        from hermes.crm.models.module_fields_db import ModuleFieldsDb
        visible = ModuleFieldsDb(db).get_visible_fields(int(mod["id"])) or []
        allowed_fields = {
            str(f.get("field_name") or "").strip() for f in visible
            if f.get("field_name")
        }
        # owner_by / created_by are always filterable even if not in the
        # visible-field list (they're system ownership columns).
        allowed_fields |= {"owner_by", "created_by", "created_at",
                           "latest_contact_date", "next_contact_date"}
        for cond in filters:
            if not isinstance(cond, dict):
                raise HTTPException(status_code=400, detail="each filter must be an object")
            fld = str(cond.get("field") or "").strip()
            op = str(cond.get("operator") or "").strip()
            if fld not in allowed_fields:
                raise HTTPException(status_code=400, detail=f"Unknown field: {fld}")
            if op not in _VIEW_OPERATORS:
                raise HTTPException(status_code=400, detail=f"Bad operator: {op}")
        logic = str(body.get("filter_logic") or "all")
        if logic not in ("all", "any"):
            raise HTTPException(status_code=400, detail="filter_logic must be all|any")

        # View type + type-specific config (Spec 3).
        vtype = str(body.get("type") or "table")
        if vtype not in ("table", "card", "kanban", "timeline", "calendar"):
            raise HTTPException(status_code=400, detail=f"Bad view type: {vtype}")
        raw_cfg = body.get("view_config")
        cfg = raw_cfg if isinstance(raw_cfg, dict) else None
        field_types = {
            str(f.get("field_name") or "").strip(): str(f.get("field_type") or "")
            for f in visible if f.get("field_name")
        }
        if vtype == "kanban":
            gb = str((cfg or {}).get("group_by") or "").strip()
            if not gb:
                raise HTTPException(status_code=400, detail="kanban requires view_config.group_by")
            # Group by any categorical field — picklist plus
            # owner(user)/creator/department/relation.
            if field_types.get(gb) not in ("picklist", "user", "createdBy", "department", "relation"):
                raise HTTPException(status_code=400, detail="kanban group_by must be a categorical field")
        elif vtype in ("timeline", "calendar"):
            dfld = str((cfg or {}).get("date_field") or "").strip()
            if not dfld:
                raise HTTPException(status_code=400, detail=f"{vtype} requires view_config.date_field")
            if field_types.get(dfld) not in ("date", "datetime"):
                raise HTTPException(status_code=400, detail=f"{vtype} date_field must be a date/datetime field")

        return {
            "module_id": int(mod["id"]),
            "name": name,
            "filters": filters,
            "filter_logic": logic,
            "type": vtype,
            "view_config": cfg,
            "is_default": bool(body.get("is_default", False)),
            "is_public": bool(body.get("is_public", False)),
            "role_ids": body.get("role_ids") or None,
            "sort_order": int(body.get("sort_order", 0)),
        }

    def _clear_other_defaults(db: DbWrapper, module_id: int, keep_id: int) -> None:
        db.execute(
            "UPDATE module_views SET is_default = FALSE "
            "WHERE module_id = %s AND id <> %s",
            (module_id, keep_id),
        )

    def _set_view_config(db: DbWrapper, view_id: int, cfg: dict | None) -> None:
        """Persist view_config via explicit jsonb SQL — ModuleViewsDb.insert
        doesn't carry this column and .update doesn't json-encode dicts."""
        import json as _json
        db.execute(
            "UPDATE module_views SET view_config = %s::jsonb WHERE id = %s",
            (_json.dumps(cfg) if cfg is not None else None, int(view_id)),
        )

    @router.get("/modules/{module_name}/views")
    def list_module_views(
        module_name: str,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        if user.get("is_admin"):
            views = ModuleViewsDb(db).get_module_views(int(mod["id"]))
        else:
            views = ModuleViewsDb(db).get_views_for_module(
                int(mod["id"]), user_id=int(user["id"]),
                role_id=user.get("role_id"),
            )
        return {"module": module_name, "views": views or []}

    @router.post("/modules/{module_name}/views")
    def create_module_view(
        module_name: str,
        body: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        data = _validate_view_body(db, mod, body)
        # Public views (org-wide / role-scoped) are admin-only.
        if data["is_public"] and not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Public views are admin-only")
        # Default views are admin-only too.
        if data["is_default"] and not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Default view is admin-only")
        data["created_by"] = int(user["id"])
        data["updated_by"] = int(user["id"])
        # view_config can't go through ModuleViewsDb (insert lacks the column,
        # update won't json-encode a dict) — write it via SQL afterwards.
        cfg = data.pop("view_config", None)
        view_id = ModuleViewsDb(db).insert(data)
        # insert() doesn't persist filter_logic; set it explicitly.
        ModuleViewsDb(db).update(view_id, {"filter_logic": data["filter_logic"]})
        _set_view_config(db, view_id, cfg)
        if data["is_default"]:
            _clear_other_defaults(db, int(mod["id"]), int(view_id))
        return {"success": True, "view": ModuleViewsDb(db).get_view_by_id(int(view_id))}

    def _can_edit_view(user: dict, view: dict) -> bool:
        if user.get("is_admin"):
            return True
        # Non-admins may edit only their own private views.
        return (not view.get("is_public")) and view.get("created_by") == int(user["id"])

    @router.put("/modules/{module_name}/views/{view_id}")
    def update_module_view(
        module_name: str,
        view_id: int,
        body: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        view = ModuleViewsDb(db).get_view_by_id(int(view_id))
        if not view or int(view.get("module_id") or 0) != int(mod["id"]):
            raise HTTPException(status_code=404, detail="view not found")
        if not _can_edit_view(user, view):
            raise HTTPException(status_code=403, detail="not allowed")
        data = _validate_view_body(db, mod, body)
        if (data["is_public"] or data["is_default"]) and not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="public/default view is admin-only")
        data["updated_by"] = int(user["id"])
        data.pop("module_id", None)
        cfg = data.pop("view_config", None)
        ModuleViewsDb(db).update(int(view_id), data)
        _set_view_config(db, int(view_id), cfg)
        if data["is_default"]:
            _clear_other_defaults(db, int(mod["id"]), int(view_id))
        return {"success": True, "view": ModuleViewsDb(db).get_view_by_id(int(view_id))}

    @router.delete("/modules/{module_name}/views/{view_id}")
    def delete_module_view(
        module_name: str,
        view_id: int,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        view = ModuleViewsDb(db).get_view_by_id(int(view_id))
        if not view or int(view.get("module_id") or 0) != int(mod["id"]):
            raise HTTPException(status_code=404, detail="view not found")
        if not _can_edit_view(user, view):
            raise HTTPException(status_code=403, detail="not allowed")
        ModuleViewsDb(db).delete(int(view_id))
        return {"success": True}

    @router.delete("/records/{module_name}/{record_id}")
    def delete_record(
        module_name: str,
        record_id: int,
        user: dict = Depends(require_permission("module_name", "delete")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        mod = _resolve_module(db, module_name)
        try:
            ItemDelete(db).set_params(
                module_id=int(mod["id"]),
                record_id=record_id,
                user_id=int(user["id"]),
            ).execute()
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"success": True}

    @router.post("/records/{module_name}/bulk_delete")
    def bulk_delete_records(
        module_name: str,
        body: dict,
        user: dict = Depends(require_permission("module_name", "delete")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Soft-delete N records in one round trip.

        Body: ``{"ids": [<int>, ...]}`` — bounded at 200 ids per call so
        a runaway selection can't lock the table for minutes.

        Returns ``{"deleted": <n>, "failures": [{"id": <id>, "reason":
        <"not_found"|"forbidden"|"error">, "detail": <msg>}, ...]}``.
        Each id is attempted independently; the call always returns 200
        with a per-id breakdown so the SPA can show a "2 deleted, 1
        failed" toast instead of an opaque transaction rollback.
        """
        ids_raw = body.get("ids") if isinstance(body, dict) else None
        if not isinstance(ids_raw, list) or not ids_raw:
            raise HTTPException(status_code=400, detail="ids: non-empty list required")
        if len(ids_raw) > 200:
            raise HTTPException(
                status_code=400,
                detail=f"too many ids ({len(ids_raw)}); cap is 200 per call",
            )
        ids: list[int] = []
        for x in ids_raw:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"non-int id: {x!r}")

        mod = _resolve_module(db, module_name)
        module_id = int(mod["id"])
        uid = int(user["id"])
        deleted = 0
        failures: list[dict] = []
        # Per-id loop (not one big transaction) so a single failure
        # doesn't roll back the rest. ItemDelete commits internally.
        for rid in ids:
            try:
                ItemDelete(db).set_params(
                    module_id=module_id,
                    record_id=rid,
                    user_id=uid,
                ).execute()
                deleted += 1
            except PermissionError as e:
                failures.append({"id": rid, "reason": "forbidden",
                                 "detail": str(e)})
            except ValueError as e:
                failures.append({"id": rid, "reason": "not_found",
                                 "detail": str(e)})
            except Exception as e:
                logger.exception("bulk_delete_records: id=%s failed", rid)
                failures.append({"id": rid, "reason": "error",
                                 "detail": str(e)})
        return {"deleted": deleted, "failures": failures}

    # --- Schema evolution (ADR 2026-04-20) -----------------------------
    #
    # Three tiers of trust:
    #   * picklist options — user & agent may both append directly
    #   * fields (add / patch / hide) — admin-only via these routes
    #   * agent-initiated field adds — go through field_proposals for
    #     admin review (see /crm/field_proposals below)

    def _pick_picklist_field(db: DbWrapper, mod: dict, field_name: str) -> dict:
        """Resolve a visible picklist field or raise the right HTTPException.

        Centralizes the "is this actually a picklist on this module" check
        so the option-mutation routes and the copilot tool share one story.
        """
        fmeta = ModuleFieldsDb(db).get_field_by_name(int(mod["id"]), field_name)
        if not fmeta:
            raise HTTPException(status_code=404, detail=f"unknown field '{field_name}'")
        if fmeta.get("is_hidden"):
            raise HTTPException(status_code=400, detail=f"field '{field_name}' is hidden")
        if fmeta.get("field_type") != "picklist":
            raise HTTPException(
                status_code=400,
                detail=f"field '{field_name}' is not a picklist (type={fmeta.get('field_type')})",
            )
        return fmeta

    @router.post("/modules/{module_name}/fields/{field_name}/options")
    def add_field_option(
        module_name: str,
        field_name: str,
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Append a single picklist option. Available to any authed user.

        Body:
          * ``value``  (required) — the stable storage key (English snake_case
            recommended; will be what records actually store)
          * ``label``  (optional) — display label for the requested locale.
            Without it the UI falls back to ``value``. Given together with
            ``value``, admin gets a one-shot "add English key + Chinese
            label" flow instead of having to follow up with a rename PATCH.
          * ``locale`` (optional) — IETF tag for the label slot (defaults
            to ``zh-CN``, the SPA's default locale)
          * ``reason`` (optional) — written to ``schema_changelog.reason``

        Returns ``{ok, status, options}`` where ``status`` is ``"added"``
        or ``"duplicate"`` (case-insensitive match — no-op, no audit row).
        """
        value_raw = payload.get("value")
        if not isinstance(value_raw, str) or not value_raw.strip():
            raise HTTPException(status_code=400, detail="'value' (non-empty string) required")
        label_raw = payload.get("label")
        if label_raw is not None and not isinstance(label_raw, str):
            raise HTTPException(status_code=400, detail="'label' must be a string when provided")
        label = (label_raw or "").strip()
        locale = (payload.get("locale") or "zh-CN").strip() or "zh-CN"
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise HTTPException(status_code=400, detail="'reason' must be a string")

        mod = _resolve_module(db, module_name)
        fmeta = _pick_picklist_field(db, mod, field_name)

        existing = parse_options(fmeta.get("options"))
        try:
            new_list, status_str = append_option(existing, value_raw)
        except PicklistError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if status_str == "duplicate":
            return {"ok": True, "status": "duplicate", "options": existing}

        # The canonical stored value is what append_option returned
        # (after trim / case-folding). Bind the label to that exact key
        # so the UI lookup matches.
        canonical_value = next((v for v in new_list if v not in existing), value_raw.strip())

        # If a label was supplied, splice it into field_config.option_labels
        # in the same shape the seed uses: ``{value: {locale: label}}``.
        # Done in the same transaction as the options + audit write so
        # observers never see "option exists but label is stale".
        new_field_config: Optional[str] = None
        if label:
            cfg_raw = fmeta.get("field_config")
            if isinstance(cfg_raw, str):
                try:
                    cfg = json.loads(cfg_raw) or {}
                except (ValueError, TypeError):
                    cfg = {}
            elif isinstance(cfg_raw, dict):
                cfg = dict(cfg_raw)
            else:
                cfg = {}
            labels = dict(cfg.get("option_labels") or {})
            entry = labels.get(canonical_value)
            if isinstance(entry, str):
                merged = {"zh-CN": entry}
            elif isinstance(entry, dict):
                merged = dict(entry)
            else:
                merged = {}
            merged[locale] = label
            labels[canonical_value] = merged
            cfg["option_labels"] = labels
            new_field_config = json.dumps(cfg)

        # Write the CSV + audit row in one transaction so an observer
        # never sees a mutation without its log entry.
        with db.transaction():
            ModuleFieldsDb(db).set_field_options(
                int(fmeta["id"]), join_options(new_list)
            )
            if new_field_config is not None:
                ModuleFieldsDb(db).update_field(
                    int(fmeta["id"]),
                    {"field_config": new_field_config},
                )
            SchemaChangelogDb(db).insert_entry(
                actor_type="user",
                actor_id=int(user["id"]),
                actor_label=user.get("username"),
                module_id=int(mod["id"]),
                module_name=module_name,
                field_id=int(fmeta["id"]),
                field_name=field_name,
                op="add_option",
                before={"options": existing},
                after={
                    "options": new_list,
                    **({"label": label, "locale": locale} if label else {}),
                },
                reason=reason,
            )

        return {
            "ok": True, "status": "added", "options": new_list,
            **({"label": label, "locale": locale} if label else {}),
        }

    def _field_system_options(fmeta: dict) -> list:
        """Return the ``field_config.system_options`` array (or empty)
        for a picklist field. System options are values the seed marks
        as code-protected — admin can rename the label but cannot
        delete the value or rename the value itself, because backend
        code branches on the literal value (e.g.
        ``TERMINAL_MILESTONES``)."""
        cfg = fmeta.get("field_config")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except (ValueError, TypeError):
                cfg = None
        if not isinstance(cfg, dict):
            return []
        sysopts = cfg.get("system_options") or []
        return [str(v) for v in sysopts if isinstance(v, (str, int))]

    @router.delete(
        "/modules/{module_name}/fields/{field_name}/options/{value}"
    )
    def delete_field_option(
        module_name: str,
        field_name: str,
        value: str,
        reason: str | None = None,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Remove a single picklist option.

        Two hard guards:
          * ``value`` in ``field_config.system_options`` → 409. These
            values (e.g. ``potentials.milestone`` ``'won'`` / ``'lost'``)
            are referenced by literal in backend code; admins must not
            silently break those branches.
          * any non-deleted row in the owning module currently stores
            this value → 409 with a count. Admins must first reassign
            those records to a different option (no silent data loss).

        Audit row written before the value disappears so the changelog
        always reflects the actual mutation.
        """
        value = (value or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="'value' required")
        if reason is not None and not isinstance(reason, str):
            raise HTTPException(status_code=400, detail="'reason' must be a string")

        mod = _resolve_module(db, module_name)
        fmeta = _pick_picklist_field(db, mod, field_name)

        existing = parse_options(fmeta.get("options"))
        if value not in existing:
            raise HTTPException(
                status_code=404,
                detail=f"option '{value}' not found in '{field_name}'",
            )
        if value in _field_system_options(fmeta):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"option '{value}' is a system option on "
                    f"'{field_name}' and cannot be deleted "
                    f"(code depends on this value)"
                ),
            )

        # Block deletion if any live record still uses this value. We
        # check the underlying column rather than module_fields metadata
        # because the value is what records actually carry.
        table_name = mod.get("table_name") or mod.get("name")
        if not table_name or not table_name.replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="invalid table")
        if not field_name.replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="invalid field")
        # Every record table created by 0011 has ``deleted BOOLEAN``;
        # treat NULL as not-deleted to be defensive against legacy data.
        in_use = db.fetchcolumn(
            f'SELECT COUNT(*) FROM "{table_name}" '
            f'WHERE "{field_name}" = %s AND (deleted IS NULL OR deleted = FALSE)',
            (value,),
        ) or 0
        if in_use > 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"option '{value}' is in use by {in_use} "
                    f"{module_name} records — reassign them to a "
                    f"different option before deleting"
                ),
            )

        new_list = [v for v in existing if v != value]
        # Also strip the value from field_config.option_labels so it
        # doesn't linger as an orphan dict entry. ``option_labels`` should
        # always be a subset of ``options``; without this cleanup the
        # dict accretes dead labels every time admin removes a value.
        cfg_raw = fmeta.get("field_config")
        if isinstance(cfg_raw, str):
            try:
                cfg = json.loads(cfg_raw) or {}
            except (ValueError, TypeError):
                cfg = {}
        elif isinstance(cfg_raw, dict):
            cfg = dict(cfg_raw)
        else:
            cfg = {}
        labels = dict(cfg.get("option_labels") or {})
        new_cfg_json: Optional[str] = None
        if value in labels:
            labels.pop(value, None)
            cfg["option_labels"] = labels
            new_cfg_json = json.dumps(cfg)

        with db.transaction():
            ModuleFieldsDb(db).set_field_options(
                int(fmeta["id"]), join_options(new_list)
            )
            if new_cfg_json is not None:
                ModuleFieldsDb(db).update_field(
                    int(fmeta["id"]),
                    {"field_config": new_cfg_json},
                )
            SchemaChangelogDb(db).insert_entry(
                actor_type="user",
                actor_id=int(user["id"]),
                actor_label=user.get("username"),
                module_id=int(mod["id"]),
                module_name=module_name,
                field_id=int(fmeta["id"]),
                field_name=field_name,
                op="delete_option",
                before={"options": existing},
                after={"options": new_list},
                reason=reason,
            )
        return {"ok": True, "status": "deleted", "options": new_list}

    # IMPORTANT route-ordering note: the literal ``/options/order`` route
    # MUST be registered BEFORE the parametric ``/options/{value}`` one.
    # FastAPI matches in registration order, so otherwise a PATCH to
    # ``/options/order`` would dispatch to update_field_option_label
    # with value="order" and reject "missing label". Keep the reorder
    # endpoint above its sibling.
    @router.patch(
        "/modules/{module_name}/fields/{field_name}/options/order"
    )
    def reorder_field_options(
        module_name: str,
        field_name: str,
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Rewrite the picklist options CSV in the given order.

        Body:
          * ``order``  (required) — list of values in the desired order.
            Must be a permutation of the field's current options (no
            adds, no drops). Reorder is the only legal mutation here;
            adds go through POST and deletes through DELETE.
          * ``reason`` (optional)

        Why a full-list PATCH and not a single-step ``swap``: drag-drop
        UIs naturally produce a final ordering; arrow buttons produce
        one too (frontend swaps locally then PATCHes). Either way the
        server only ever sees the canonical new order, so we don't need
        to multiplex on a "direction" enum.

        The funnel order drives the SPA dropdown order AND
        ``coach.py``'s stage-regression detection
        (``_milestone_sort_index`` reads this CSV), so a reorder is a
        first-class semantic change worth its own audit row.
        """
        order = payload.get("order")
        if not isinstance(order, list):
            raise HTTPException(
                status_code=400, detail="'order' (list of values) required",
            )
        normalized: list[str] = []
        for v in order:
            if not isinstance(v, str) or not v.strip():
                raise HTTPException(
                    status_code=400,
                    detail="'order' entries must be non-empty strings",
                )
            normalized.append(v.strip())
        if len(set(normalized)) != len(normalized):
            raise HTTPException(
                status_code=400, detail="'order' contains duplicates",
            )
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise HTTPException(status_code=400, detail="'reason' must be a string")

        mod = _resolve_module(db, module_name)
        fmeta = _pick_picklist_field(db, mod, field_name)

        existing = parse_options(fmeta.get("options"))
        if set(normalized) != set(existing):
            missing = [v for v in existing if v not in set(normalized)]
            extra = [v for v in normalized if v not in set(existing)]
            raise HTTPException(
                status_code=400,
                detail=(
                    "'order' must be a permutation of current options; "
                    f"missing={missing} extra={extra}"
                ),
            )
        if normalized == existing:
            return {"ok": True, "status": "noop", "options": existing}

        with db.transaction():
            ModuleFieldsDb(db).set_field_options(
                int(fmeta["id"]), join_options(normalized)
            )
            SchemaChangelogDb(db).insert_entry(
                actor_type="user",
                actor_id=int(user["id"]),
                actor_label=user.get("username"),
                module_id=int(mod["id"]),
                module_name=module_name,
                field_id=int(fmeta["id"]),
                field_name=field_name,
                op="reorder_options",
                before={"options": existing},
                after={"options": normalized},
                reason=reason,
            )
        return {"ok": True, "status": "reordered", "options": normalized}

    @router.patch(
        "/modules/{module_name}/fields/{field_name}/options/{value}"
    )
    def update_field_option_label(
        module_name: str,
        field_name: str,
        value: str,
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Rename the display label of a picklist option.

        The ``value`` (stored key) is NEVER changed — every record's
        stored data refers to it, and backend code may branch on it.
        Only the per-locale slot inside
        ``field_config.option_labels[value]`` gets updated. System
        options can have their label edited freely (e.g.
        ``"赢单"`` → ``"已成交"``) since labels are purely UI.

        Body:
          * ``label`` (required) — new display label (non-empty string)
          * ``locale`` (optional) — IETF tag, defaults to ``zh-CN``;
            controls which translation slot is written. Other locales
            on the same option are preserved.
          * ``reason`` (optional)

        The on-disk shape we maintain is::

            {"won": {"zh-CN": "赢单", "en-US": "Won"}, ...}

        Legacy flat-string entries (``{"won": "赢单"}``) are upgraded to
        the dict shape on first write so downstream readers don't have
        to branch on type.
        """
        value = (value or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="'value' required")
        new_label = payload.get("label")
        if not isinstance(new_label, str) or not new_label.strip():
            raise HTTPException(
                status_code=400, detail="'label' (non-empty string) required",
            )
        new_label = new_label.strip()
        locale = (payload.get("locale") or "zh-CN").strip() or "zh-CN"
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise HTTPException(status_code=400, detail="'reason' must be a string")

        mod = _resolve_module(db, module_name)
        fmeta = _pick_picklist_field(db, mod, field_name)

        existing_values = parse_options(fmeta.get("options"))
        if value not in existing_values:
            raise HTTPException(
                status_code=404,
                detail=f"option '{value}' not found in '{field_name}'",
            )

        cfg_raw = fmeta.get("field_config")
        if isinstance(cfg_raw, str):
            try:
                cfg = json.loads(cfg_raw) or {}
            except (ValueError, TypeError):
                cfg = {}
        elif isinstance(cfg_raw, dict):
            cfg = dict(cfg_raw)
        else:
            cfg = {}
        labels = dict(cfg.get("option_labels") or {})

        # Upgrade legacy flat-string entry to dict, treating the old
        # string as a zh-CN value (matches DEFAULT_LOCALE on the SPA).
        existing_entry = labels.get(value)
        if isinstance(existing_entry, str):
            entry: dict = {"zh-CN": existing_entry}
        elif isinstance(existing_entry, dict):
            entry = dict(existing_entry)
        else:
            entry = {}

        before = {"label": existing_entry, "locale": locale}
        if entry.get(locale) == new_label:
            return {"ok": True, "status": "noop", "label": new_label}
        entry[locale] = new_label
        labels[value] = entry
        cfg["option_labels"] = labels

        with db.transaction():
            ModuleFieldsDb(db).update_field(
                int(fmeta["id"]),
                {"field_config": json.dumps(cfg)},
            )
            SchemaChangelogDb(db).insert_entry(
                actor_type="user",
                actor_id=int(user["id"]),
                actor_label=user.get("username"),
                module_id=int(mod["id"]),
                module_name=module_name,
                field_id=int(fmeta["id"]),
                field_name=field_name,
                op="rename_option_label",
                before=before,
                after={"label": new_label, "locale": locale, "value": value},
                reason=reason,
            )
        return {"ok": True, "status": "updated", "label": new_label, "locale": locale}

    @router.get("/modules/{module_name}/fields")
    def list_module_fields(
        module_name: str,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Field metadata for a module — any authenticated user.

        A schema-level read, independent of any record — consumed by the
        Schema Hub builder AND by the list-item field customizer / view
        builder (which non-admins also use). Field schema is already
        exposed to ordinary users via the per-record detail route's
        ``fields[]``, so widening this from admin-only to any logged-in
        user is consistent and non-sensitive. It must NOT piggy-back on
        the per-record detail route, which 404s on a missing record.
        Returns ``{module_name, fields}``.
        """
        mod = _resolve_module(db, module_name)
        fields = ModuleFieldsDb(db).get_module_fields(int(mod["id"])) or []
        # Add a locale-resolved ``display_label`` so the Schema Hub
        # builder can show English labels in en-US while leaving the raw
        # ``field_label`` intact for editing in the property panel.
        # Same resolver list_query / detail_query use, so labels agree
        # across record views and the builder.
        from hermes.crm.i18n.field_labels import localize_field_label
        table_name = str(mod.get("table_name") or module_name)
        for f in fields:
            fname = str(f.get("field_name") or "")
            if not fname:
                continue
            f["display_label"] = localize_field_label(
                table_name=table_name,
                field_name=fname,
                field_label=str(f.get("field_label") or fname),
                field_id=int(f.get("id") or 0) or None,
                field_config=f.get("field_config"),
            )
        return {"module_name": module_name, "fields": fields}

    @router.post("/modules/{module_name}/fields")
    def add_module_field(
        module_name: str,
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: add a new field to a module.

        Body: ``{field_name, field_label, field_type, is_required?,
                 is_searchable?, is_readonly?, options?, default_value?,
                 description?, field_config?, sort_order?, reason?}``

        Delegates to ``provision_field`` — the one helper that inserts
        the metadata row AND creates the physical column. Previously
        this route only inserted metadata, so fields it created would
        500 the moment a query touched the missing column.

        Returns ``{ok, id, field_name}``.
        """
        mod = _resolve_module(db, module_name)

        # field_name/label/type required-ness + identifier validity are
        # enforced by provision_field below (single source of truth).
        # The dup-check here only runs when a non-empty name was given.
        name = str(payload.get("field_name") or "").strip()
        if name and ModuleFieldsDb(db).get_field_by_name(
            int(mod["id"]), name
        ):
            raise HTTPException(
                status_code=409,
                detail=f"field '{name}' already exists",
            )

        # options: a list of strings, only meaningful for picklists.
        # Validate + CSV-encode here so provision_field receives the
        # already-stored column form.
        opts_raw = payload.get("options")
        options_csv: Optional[str] = None
        if opts_raw is not None:
            if not isinstance(opts_raw, list):
                raise HTTPException(
                    status_code=400, detail="'options' must be a list")
            clean: list[str] = []
            for v in opts_raw:
                try:
                    clean, _ = append_option(clean, str(v))
                except PicklistError as e:
                    raise HTTPException(
                        status_code=400, detail=f"options: {e}")
            options_csv = join_options(clean)

        field_data = {
            "field_name":    name,
            "field_label":   payload.get("field_label"),
            "field_type":    payload.get("field_type"),
            "is_required":   bool(payload.get("is_required", False)),
            "is_searchable": bool(payload.get("is_searchable", False)),
            "is_readonly":   bool(payload.get("is_readonly", False)),
            "options":       options_csv,
            "default_value": payload.get("default_value"),
            "description":   payload.get("description"),
            "field_config":  payload.get("field_config"),
            "sort_order":    int(payload.get("sort_order", 0)),
        }

        result = provision_field(
            db, mod, field_data, user, payload.get("reason"),
        )

        # Best-effort: tell dashboards the schema grew so they rebuild
        # their views. Failure here doesn't roll back the column add
        # (already committed inside provision_field).
        try:
            from hermes_cloud.app.dashboards.routes import (
                force_refresh_registry as _refresh_dash_registry,
            )
            _refresh_dash_registry(db)
        except Exception:
            logger.exception(
                "dashboards: refresh after field add failed")

        return {
            "ok": True,
            "id": result["field_id"],
            "field_name": result["field_name"],
        }

    @router.patch("/modules/{module_name}/fields/{field_name}")
    def patch_module_field(
        module_name: str,
        field_name: str,
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: edit label / visibility / description on an existing field.

        Explicitly does NOT change ``field_name`` or ``field_type`` —
        renames break every saved view and cached query; type changes
        break existing row data. Those need a migration, not a PATCH.
        """
        mod = _resolve_module(db, module_name)
        fmeta = ModuleFieldsDb(db).get_field_by_name(int(mod["id"]), field_name)
        if not fmeta:
            raise HTTPException(status_code=404, detail=f"unknown field '{field_name}'")

        patch: dict = {}
        for k, v in (payload or {}).items():
            if k not in PATCH_FIELD_ALLOWED_KEYS:
                continue
            # field_config is a dict on the wire; the JSON column
            # wants JSON text. Everything else passes through as-is.
            if k == "field_config" and v is not None:
                if not isinstance(v, dict):
                    raise HTTPException(
                        status_code=400,
                        detail="field_config must be an object",
                    )
                patch[k] = json.dumps(v)
            else:
                patch[k] = v
        if not patch:
            raise HTTPException(
                status_code=400,
                detail=f"no editable keys in body "
                       f"(allowed: {sorted(PATCH_FIELD_ALLOWED_KEYS)})",
            )

        # ── ai_fill 校验 + 惰性 ai_meta 列(PATCH 启用 AI 字段必须和 create 同闸,
        #    否则 mode↔type 不匹配→永远弃填,ai_meta 列缺失→首次 fill 500)──────
        if "field_config" in patch:
            _fc = patch["field_config"]
            _fc = json.loads(_fc) if isinstance(_fc, str) else _fc
            _aifill = (_fc or {}).get("ai_fill") if isinstance(_fc, dict) else None
            if isinstance(_aifill, dict) and _aifill.get("enabled"):
                from hermes_cloud.app.crm.ai_attributes import validate_ai_fill, AiFillError
                from hermes_cloud.app.crm.field_provisioning import canonical_field_type
                _existing = {
                    str(f.get("field_name"))
                    for f in (ModuleFieldsDb(db).get_module_fields(int(mod["id"])) or [])
                }
                _ftype = canonical_field_type(str(fmeta.get("field_type") or "").strip().lower())
                try:
                    validate_ai_fill(_ftype, _aifill, _existing)
                except AiFillError as e:
                    raise HTTPException(status_code=400, detail=f"ai_fill: {e}")
                _table = str(mod.get("table_name") or module_name)
                db.execute(f'ALTER TABLE "{_table}" ADD COLUMN IF NOT EXISTS ai_meta JSONB')

        op = "hide_field" if patch.get("is_hidden") is True else "update_field"
        before = {k: fmeta.get(k) for k in PATCH_FIELD_ALLOWED_KEYS}
        after = {**before, **patch}

        # ── Formula side-effects (Task P3.5) ────────────────────────────
        # A PATCH that changes a formula field's expression must not
        # introduce a dependency cycle, and must recompute every row.
        from hermes.crm.formula import detect_cycles, FormulaError
        from hermes_cloud.app.crm.field_provisioning import recompute_formula_field

        formula_expr_changed = False
        new_expr = ""
        if str(fmeta.get("field_type") or "") == "formula" and "field_config" in patch:
            # patch["field_config"] is JSON text (serialised above); decode it
            # before passing to formula_expression_of which expects a dict.
            raw_new_fc = patch.get("field_config")
            new_fc = json.loads(raw_new_fc) if isinstance(raw_new_fc, str) else raw_new_fc
            new_expr = formula_expression_of(new_fc)
            # fmeta["field_config"] may also be a JSON string from the DB.
            raw_old_fc = fmeta.get("field_config")
            old_fc = json.loads(raw_old_fc) if isinstance(raw_old_fc, str) else raw_old_fc
            old_expr = formula_expression_of(old_fc)
            formula_expr_changed = new_expr != old_expr

        if formula_expr_changed:
            # Build {field_name: expression} for every formula field on the
            # module, with THIS field's expression replaced by the new one,
            # then reject if that introduces a cycle.
            formula_map: dict[str, str] = {}
            for f in ModuleFieldsDb(db).get_module_fields(int(mod["id"])) or []:
                if str(f.get("field_type") or "") != "formula":
                    continue
                if f["field_name"] == field_name:
                    formula_map[field_name] = new_expr
                else:
                    raw_fc = f.get("field_config")
                    fc = json.loads(raw_fc) if isinstance(raw_fc, str) else raw_fc
                    formula_map[f["field_name"]] = formula_expression_of(fc)
            try:
                cycle = detect_cycles(formula_map)
            except FormulaError as e:
                raise HTTPException(status_code=400, detail=f"formula: {e}")
            if cycle is not None:
                raise HTTPException(
                    status_code=400,
                    detail=f"formula introduces a dependency cycle: {' → '.join(cycle)}",
                )

        with db.transaction():
            ModuleFieldsDb(db).update_field(int(fmeta["id"]), patch)
            SchemaChangelogDb(db).insert_entry(
                actor_type="user",
                actor_id=int(user["id"]),
                actor_label=user.get("username"),
                module_id=int(mod["id"]),
                module_name=module_name,
                field_id=int(fmeta["id"]),
                field_name=field_name,
                op=op,
                before=before,
                after=after,
                reason=payload.get("reason"),
            )

        if formula_expr_changed:
            # Refresh the stored NUMERIC column on every existing row.
            fresh = ModuleFieldsDb(db).get_field_by_name(int(mod["id"]), field_name)
            recompute_formula_field(db, mod, fresh)

        return {"ok": True, "field_name": field_name, "after": after}

    @router.put("/modules/{module_name}/fields/order")
    def reorder_module_fields(
        module_name: str,
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: bulk-reorder a module's non-hidden fields.

        Body: ``{order: [field_name, ...], reason?}``. ``order`` is
        the ordered list of the module's NON-HIDDEN fields — hidden
        fields keep their sort_order untouched.

        One transaction: write sort_order 0..N-1 by index, plus one
        ``reorder_fields`` changelog entry.
        """
        mod = _resolve_module(db, module_name)
        mf_db = ModuleFieldsDb(db)

        visible = mf_db.get_visible_fields(int(mod["id"])) or []
        visible_by_name = {f["field_name"]: f for f in visible}

        order = payload.get("order")
        validate_reorder(order, set(visible_by_name.keys()))

        before_order = [f["field_name"] for f in visible]

        with db.transaction():
            for idx, fname in enumerate(order):
                field_id = int(visible_by_name[fname]["id"])
                mf_db.update_field(field_id, {"sort_order": idx})
            SchemaChangelogDb(db).insert_entry(
                actor_type="user",
                actor_id=int(user["id"]),
                actor_label=user.get("username"),
                module_id=int(mod["id"]),
                module_name=module_name,
                field_id=None,
                field_name=None,
                op="reorder_fields",
                before={"order": before_order},
                after={"order": list(order)},
                reason=payload.get("reason"),
            )

        return {"ok": True, "order": list(order)}

    @router.get("/schema_changelog")
    def get_schema_changelog(
        module_name: Optional[str] = None,
        actor_type: Optional[str] = None,
        limit: int = 100,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Audit log of every mutation written through the gateways above.

        Readable by any authed user — transparency is part of the point.
        Mutation is admin-only, reading is not.
        """
        module_id: Optional[int] = None
        if module_name:
            mod = _resolve_module(db, module_name)
            module_id = int(mod["id"])
        if actor_type is not None and actor_type not in ("user", "agent"):
            raise HTTPException(status_code=400, detail="actor_type must be 'user' or 'agent'")
        rows = SchemaChangelogDb(db).list_recent(
            module_id=module_id, actor_type=actor_type, limit=limit,
        )
        return {"data": rows}

    @router.post("/field_proposals")
    def create_field_proposal(
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Queue a field-addition proposal for admin review.

        Body: ``{module_name, field_name, field_label, field_type,
                 reason, options?, is_required?}``
        """
        module_name = str(payload.get("module_name") or "").strip()
        if not module_name:
            raise HTTPException(status_code=400, detail="'module_name' required")
        for k in ("field_name", "field_label", "field_type", "reason"):
            if not str(payload.get(k) or "").strip():
                raise HTTPException(status_code=400, detail=f"'{k}' required")

        mod = _resolve_module(db, module_name)
        existing = ModuleFieldsDb(db).get_field_by_name(
            int(mod["id"]), str(payload["field_name"]).strip()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"field '{payload['field_name']}' already exists on {module_name}",
            )

        pid = FieldProposalsDb(db).insert_proposal(
            proposer_type="user",
            proposer_id=int(user["id"]),
            proposer_label=user.get("username"),
            module_id=int(mod["id"]),
            module_name=module_name,
            payload={k: v for k, v in payload.items() if k != "module_name"},
        )
        return {"ok": True, "id": pid, "status": "pending"}

    @router.get("/field_proposals")
    def list_field_proposals(
        status: str = "pending",
        limit: int = 100,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: list proposals by status (default pending)."""
        try:
            rows = FieldProposalsDb(db).list_by_status(status=status, limit=limit)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"data": rows}

    @router.get("/field_proposals/pending_count")
    def count_pending_field_proposals(
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: number of proposals awaiting review.

        Backs the review-queue badge in the SPA nav so an admin sees at a
        glance that the AI has filed schema-change proposals worth a look.
        """
        return {"count": FieldProposalsDb(db).count_by_status("pending")}

    @router.post("/field_proposals/{proposal_id}/review")
    def review_field_proposal(
        proposal_id: int,
        payload: dict,
        user: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """Admin-only: approve or reject a proposal.

        Body: ``{decision: "approved"|"rejected", note?: string}``
        On approval, the proposal payload is inserted into ``module_fields``
        and a ``schema_changelog`` row is written — same transaction as
        the status update so the three tables stay consistent.
        """
        decision = str(payload.get("decision") or "").strip()
        if decision not in ("approved", "rejected"):
            raise HTTPException(
                status_code=400,
                detail="'decision' must be 'approved' or 'rejected'",
            )
        note = payload.get("note")
        if note is not None and not isinstance(note, str):
            raise HTTPException(status_code=400, detail="'note' must be a string")

        proposals_db = FieldProposalsDb(db)
        prop = proposals_db.get_by_id(proposal_id)
        if not prop:
            raise HTTPException(status_code=404, detail="proposal not found")
        if prop.get("status") != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"proposal already {prop.get('status')}",
            )

        created_field_id: Optional[int] = None

        if decision == "rejected":
            proposals_db.mark_reviewed(
                proposal_id,
                status="rejected",
                reviewer_id=int(user["id"]),
                review_note=note,
            )
            return {"ok": True, "status": "rejected"}

        # Approve — materialize the proposed change. Dispatch on payload
        # kind so a single review queue can hold both field-add and
        # picklist-option-add proposals (charter v2 §定律 5 self-evolving
        # schema, 2026-05-06). Pre-kind proposals are treated as
        # ``field_add`` for back-compat.
        raw = prop.get("payload") or {}
        if isinstance(raw, str):
            import json as _json
            try:
                raw = _json.loads(raw)
            except Exception:
                raise HTTPException(status_code=500, detail="proposal payload corrupt")

        kind = str(raw.get("kind") or "field_add").strip()

        if kind == "picklist_option_add":
            target_field_name = str(raw.get("field_name") or "").strip()
            new_value = str(raw.get("value") or "").strip()
            new_label = str(raw.get("label") or "").strip()
            if not target_field_name or not new_value or not new_label:
                raise HTTPException(
                    status_code=400,
                    detail="picklist_option_add proposal missing field_name/value/label",
                )
            target_field = ModuleFieldsDb(db).get_field_by_name(
                int(prop["module_id"]), target_field_name,
            )
            if not target_field:
                raise HTTPException(
                    status_code=404,
                    detail=f"field '{target_field_name}' no longer exists on this module",
                )
            ftype = str(target_field.get("field_type") or "").lower()
            if ftype != "picklist":
                raise HTTPException(
                    status_code=400,
                    detail=f"field '{target_field_name}' is type '{ftype}', not picklist",
                )
            from hermes.crm.i18n.options import parse_picklist_options
            import json as _json2

            # Lost-update race fix (review #1, 2026-05-06): two admins
            # approving two different picklist proposals concurrently
            # both read the same options snapshot, each computes append,
            # second UPDATE clobbers first. Lock the row inside the
            # transaction and re-read after the lock; re-check dedup
            # against the locked state — the option may have been added
            # by a sibling proposal between the SELECT above and now.
            with db.transaction():
                row = db.fetchone(
                    "SELECT options FROM module_fields WHERE id = %s FOR UPDATE",
                    (int(target_field["id"]),),
                )
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=f"field '{target_field_name}' no longer exists",
                    )
                locked_options_raw = row.get("options")
                current = parse_picklist_options(locked_options_raw)
                if any(
                    str(o.get("value")).strip() == new_value
                    or str(o.get("text")).strip() == new_label
                    for o in current
                ):
                    # Sibling approval already added this option (or one
                    # colliding with it). Mark proposal approved as a
                    # no-op, don't double-write changelog.
                    proposals_db.mark_reviewed(
                        proposal_id,
                        status="approved",
                        reviewer_id=int(user["id"]),
                        review_note=note,
                    )
                    return {"ok": True, "status": "approved", "noop": True}

                new_options_list = [
                    {"value": str(o.get("value")), "text": str(o.get("text") or o.get("value"))}
                    for o in current
                ] + [{"value": new_value, "text": new_label}]
                new_options_json = _json2.dumps(new_options_list, ensure_ascii=False)

                db.execute(
                    "UPDATE module_fields SET options = %s WHERE id = %s",
                    (new_options_json, int(target_field["id"])),
                )
                SchemaChangelogDb(db).insert_entry(
                    actor_type="user",
                    actor_id=int(user["id"]),
                    actor_label=user.get("username"),
                    module_id=int(prop["module_id"]),
                    module_name=str(prop["module_name"]),
                    field_id=int(target_field["id"]),
                    field_name=target_field_name,
                    op="add_option",
                    before={"options": locked_options_raw},
                    after={"options": new_options_json, "added": {"value": new_value, "label": new_label}},
                    reason=(
                        f"via proposal #{proposal_id} "
                        f"(proposer={prop.get('proposer_label') or prop.get('proposer_id')})"
                    ),
                )
                proposals_db.mark_reviewed(
                    proposal_id,
                    status="approved",
                    reviewer_id=int(user["id"]),
                    review_note=note,
                )
            return {
                "ok": True,
                "status": "approved",
                "field_id": int(target_field["id"]),
                "added_option": {"value": new_value, "label": new_label},
            }

        proposer_actor = _proposer_actor(prop, user)
        review_reason = (
            f"via proposal #{proposal_id} "
            f"(proposer={prop.get('proposer_label') or prop.get('proposer_id')}, "
            f"reviewer={user.get('username')})"
        )

        if kind == "module_add":
            from hermes_cloud.app.crm.module_provisioning import provision_module
            spec = {k: raw.get(k) for k in ("name", "title", "title_en", "initial_fields")}
            res = provision_module(db, spec, proposer_actor, review_reason)
            with db.transaction():
                proposals_db.mark_reviewed(proposal_id, status="approved",
                                           reviewer_id=int(user["id"]), review_note=note)
            return {"ok": True, "status": "approved",
                    "module_id": res.get("module_id"), "name": res.get("name")}

        if kind == "subdetail_add":
            from hermes_cloud.app.crm.module_provisioning import provision_subdetail
            parent = str(raw.get("parent_name") or "").strip()
            spec = {k: raw.get(k) for k in ("name", "title", "title_en", "detail_kind", "initial_fields")}
            res = provision_subdetail(db, parent, spec, proposer_actor, review_reason)
            with db.transaction():
                proposals_db.mark_reviewed(proposal_id, status="approved",
                                           reviewer_id=int(user["id"]), review_note=note)
            return {"ok": True, "status": "approved", **res}

        if kind == "module_rename":
            from hermes_cloud.app.crm.module_provisioning import rename_module
            res = rename_module(db, str(raw.get("name")), raw.get("title"),
                                raw.get("title_en"), proposer_actor, review_reason)
            with db.transaction():
                proposals_db.mark_reviewed(proposal_id, status="approved",
                                           reviewer_id=int(user["id"]), review_note=note)
            return {"ok": True, "status": "approved", **res}

        if kind == "workflow_add":
            from hermes_cloud.app.crm.workflow_provisioning import provision_workflow
            spec = {k: raw.get(k) for k in
                    ("name", "trigger_type", "module", "trigger_on",
                     "schedule_cron", "condition", "actions")}
            res = provision_workflow(db, spec, proposer_actor)
            with db.transaction():
                proposals_db.mark_reviewed(proposal_id, status="approved",
                                           reviewer_id=int(user["id"]), review_note=note)
            return {"ok": True, "status": "approved", "workflow_id": res["id"]}

        if kind == "writeback_add":
            from hermes.crm.field_writeback import FieldWriteback
            res = FieldWriteback(db, user_id=int(proposer_actor["id"])).create_rule({
                k: raw.get(k) for k in (
                    "source_module_id", "source_field_id",
                    "target_module_id", "target_field_id", "calc_method")
            })
            if not res.get("success"):
                raise HTTPException(
                    status_code=400,
                    detail=res.get("error") or "writeback rule creation failed",
                )
            with db.transaction():
                proposals_db.mark_reviewed(proposal_id, status="approved",
                                           reviewer_id=int(user["id"]), review_note=note)
            return {"ok": True, "status": "approved", "rule_id": res.get("id")}

        # kind == "field_add" — delegate to the shared provision_field
        # helper. Previously this branch carried its own 7-type-only
        # sql_type_map and inline ALTER TABLE; provision_field is now
        # the single source of truth (full type map, sequence DDL,
        # backfill extension point).
        name = str(raw.get("field_name") or "").strip()
        if not name:
            raise HTTPException(
                status_code=400,
                detail="proposal payload missing field_name",
            )
        if ModuleFieldsDb(db).get_field_by_name(
            int(prop["module_id"]), name
        ):
            # Raced — admin or another proposal beat us to it.
            raise HTTPException(
                status_code=409,
                detail=f"field '{name}' already exists; "
                       f"withdrawing approval",
            )

        opts_raw = raw.get("options")
        options_csv: Optional[str] = None
        if isinstance(opts_raw, list) and opts_raw:
            clean: list[str] = []
            for v in opts_raw:
                try:
                    clean, _ = append_option(clean, str(v))
                except PicklistError as e:
                    raise HTTPException(
                        status_code=400, detail=f"options: {e}")
            options_csv = join_options(clean)

        field_data = {
            "field_name":    name,
            "field_label":   raw.get("field_label"),
            "field_type":    raw.get("field_type"),
            "is_required":   bool(raw.get("is_required", False)),
            "is_searchable": bool(raw.get("is_searchable", False)),
            "is_readonly":   bool(raw.get("is_readonly", False)),
            "options":       options_csv,
            "default_value": raw.get("default_value"),
            "description":   raw.get("description"),
            "field_config":  raw.get("field_config"),
            "sort_order":    int(raw.get("sort_order", 0)),
        }

        # provision_field needs a resolved module row dict
        # (id / name / table_name). The proposal carries module_id +
        # module_name; module_name doubles as table_name here.
        module_row = {
            "id":         int(prop["module_id"]),
            "name":       str(prop["module_name"]),
            "table_name": str(prop["module_name"]),
        }
        result = provision_field(
            db, module_row, field_data, proposer_actor, reason=review_reason,
        )
        created_field_id = result["field_id"]

        # mark_reviewed is a separate write — keep it in its own
        # transaction right after the (committed) provision_field.
        with db.transaction():
            proposals_db.mark_reviewed(
                proposal_id,
                status="approved",
                reviewer_id=int(user["id"]),
                review_note=note,
                created_field_id=created_field_id,
            )

        # Best-effort dashboard view rebuild — failure doesn't roll
        # back the (already committed) column add.
        try:
            from hermes_cloud.app.dashboards.routes import (
                force_refresh_registry as _refresh_dash_registry,
            )
            _refresh_dash_registry(db)
        except Exception:
            logger.exception(
                "dashboards: refresh after field add failed")

        return {
            "ok": True,
            "status": "approved",
            "field_id": created_field_id,
            "field_name": name,
        }

    # ------------------------------------------------------------------
    # Record sharing (collaborators)
    # ------------------------------------------------------------------

    @router.get("/records/{module_name}/{record_id}/collaborators")
    def list_record_collaborators(
        module_name: str,
        record_id: int,
        user: dict = Depends(require_permission("module_name", "detail")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        from hermes_cloud.app.crm.collaborators import list_collaborators
        mod = _resolve_module(db, module_name)
        # 同时取 owner_by 以便行级 ACL 判断
        row = db.fetchone(
            f'SELECT owner_by FROM "{module_name}" '
            f'WHERE "{module_name}_id" = %s AND (deleted = FALSE OR deleted IS NULL)',
            (record_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Record not found")
        uid = int(user["id"])
        is_admin = bool(user.get("is_admin"))
        owner_uid = row.get("owner_by")
        if owner_uid is not None:
            owner_uid = int(owner_uid)
        is_owner = (owner_uid == uid)
        rows = list_collaborators(db, module_id=int(mod["id"]), record_id=record_id)
        is_collaborator = any(int(c["user_id"]) == uid for c in rows)
        if not (is_admin or is_owner or is_collaborator):
            raise HTTPException(status_code=403, detail="no access to collaborators of this record")
        return {"collaborators": rows}

    @router.post("/records/{module_name}/{record_id}/collaborators")
    def add_record_collaborator(
        module_name: str,
        record_id: int,
        payload: dict,
        user: dict = Depends(require_permission("module_name", "detail")),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        from hermes_cloud.app.crm.collaborators import upsert_collaborator
        mod = _resolve_module(db, module_name)
        row = db.fetchone(
            f'SELECT owner_by FROM "{module_name}" '
            f'WHERE "{module_name}_id" = %s AND (deleted = FALSE OR deleted IS NULL)',
            (record_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Record not found")
        uid = int(user["id"])
        is_admin = bool(user.get("is_admin"))
        owner_uid = row.get("owner_by")
        if owner_uid is not None:
            owner_uid = int(owner_uid)
        is_owner = (owner_uid == uid)
        has_share = Permission(db).check_permission_by_module_name(uid, module_name, "share")
        if not (is_admin or (is_owner and has_share)):
            raise HTTPException(status_code=403, detail="no permission to share this record")

        target_uid = int(payload.get("user_id") or 0)
        can_view = bool(payload.get("can_view", True))
        can_edit = bool(payload.get("can_edit", False))
        if not target_uid:
            raise HTTPException(status_code=400, detail="user_id required")
        if not (can_view or can_edit):
            raise HTTPException(status_code=400, detail="at least one of can_view/can_edit must be true")
        if target_uid == uid:
            raise HTTPException(status_code=400, detail="cannot share to yourself")

        upsert_collaborator(
            db, module_id=int(mod["id"]), record_id=record_id,
            user_id=target_uid, can_view=can_view, can_edit=can_edit,
        )

        # 通知:24h 内同 (record, target) 已发过则跳过(去重),否则写一条 interaction_log
        # channel='crm_share' / role='system' — 表无 check 约束,空表取新值对齐生态
        already = db.fetchcolumn(
            """SELECT COUNT(*) FROM interaction_log
                WHERE user_id = %s
                  AND channel = 'crm_share'
                  AND content->>'entity_type' = %s
                  AND (content->>'entity_id')::bigint = %s
                  AND created_at > NOW() - INTERVAL '24 hours'""",
            (target_uid, module_name, record_id),
        ) or 0
        if not already:
            import json as _json
            db.execute(
                """INSERT INTO interaction_log
                    (user_id, channel, role, content, meta, created_at)
                   VALUES (%s, 'crm_share', 'system', %s::jsonb, '{}'::jsonb, NOW())""",
                (target_uid, _json.dumps({
                    "entity_type": module_name,
                    "entity_id": record_id,
                    "by_uid": uid,
                    "can_view": can_view,
                    "can_edit": can_edit,
                })),
            )
            # 注:db.execute() 默认 commit=True,无需手动 commit

        return {"user_id": target_uid, "can_view": can_view, "can_edit": can_edit}

    @router.delete(
        "/records/{module_name}/{record_id}/collaborators/{target_user_id}",
        status_code=204,
    )
    def remove_record_collaborator(
        module_name: str,
        record_id: int,
        target_user_id: int,
        user: dict = Depends(require_permission("module_name", "detail")),
        db: DbWrapper = Depends(get_db),
    ):
        from hermes_cloud.app.crm.collaborators import delete_collaborator
        mod = _resolve_module(db, module_name)
        row = db.fetchone(
            f'SELECT owner_by FROM "{module_name}" '
            f'WHERE "{module_name}_id" = %s AND (deleted = FALSE OR deleted IS NULL)',
            (record_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Record not found")
        uid = int(user["id"])
        owner_uid = row.get("owner_by")
        if owner_uid is not None:
            owner_uid = int(owner_uid)
        if not (user.get("is_admin") or owner_uid == uid):
            raise HTTPException(status_code=403, detail="only owner or admin may revoke")
        delete_collaborator(
            db, module_id=int(mod["id"]), record_id=record_id, user_id=target_user_id,
        )
        return Response(status_code=204)

    # AI 字段手动补全(同步,admin)— 处理函数定义在模块级,便于直接单测;
    # 这里挂到本 router 上,沿用字段路由的 /modules/... 前缀。
    router.add_api_route(
        "/modules/{module_name}/records/{record_id}/ai-fill",
        ai_fill_record_endpoint,
        methods=["POST"],
    )
    # 一句话需求 → AI 字段配置(供构建器预填)。处理函数定义在模块级,便于直接单测。
    router.add_api_route("/ai-field/draft-config", ai_field_draft_config_endpoint, methods=["POST"])

    # ------------------------------------------------------------------
    # P0b Task 4 — 待认领队列 + claim 绑定动作
    # ------------------------------------------------------------------

    @router.get("/workflows")
    def list_all_workflows(
        module: str | None = None,
        _user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> list[dict]:
        """全局列出工作流(跨模块)。module 省略=全部;每条带 module 字段供前端打标。
        视图全局,数据仍各自绑模块——工作流触发在某模块的记录事件上。"""
        from hermes_cloud.app.crm.workflows import list_workflows
        return list_workflows(db, module or None)

    @router.get("/modules/{module_name}/workflows")
    def list_module_workflows(
        module_name: str,
        _user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> list[dict]:
        """列出某模块的自动化工作流(只读)。编辑/新建仍走 agent 对话。"""
        from hermes_cloud.app.crm.workflows import list_workflows
        return list_workflows(db, module_name)

    @router.get("/modules/{module_name}/workflows/runs")
    def list_module_workflow_runs(
        module_name: str,
        workflow_id: int | None = None,
        limit: int = 100,
        _user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """列出某模块工作流的执行历史(只读)。可选 ?workflow_id= 过滤、?limit=。

        query 参数用普通类型化函数参(同邻近 GET 路由);本文件 future-annotations
        下不能用函数内局部 Pydantic 模型。``/workflows/runs`` 是独立路径,不会被
        ``/workflows`` 遮蔽。
        """
        from hermes_cloud.app.crm.workflows import list_workflow_runs
        return {"runs": list_workflow_runs(db, module_name, workflow_id, limit)}

    @router.patch("/workflows/{workflow_id}")
    def toggle_workflow(
        workflow_id: int,
        payload: dict,
        _user: dict = Depends(_require_admin),  # 仅管理员可直接改;其他人经 AI 助手提案
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """启用/停用 或 切 draft/live 一条工作流。

        body 用 ``dict`` 而非局部 Pydantic 模型:本文件 ``from __future__ import
        annotations`` 把注解变字符串,FastAPI 无法解析函数内局部定义的模型,会把
        它误判成 query 参数(422 'body field required')。

        body 可含 ``status``(draft/live)和/或 ``enabled``(bool),按存在与否分别应用。

        亦支持结构化局部编辑(前端点选块):body 含 ``name``/``trigger_on``/
        ``schedule_cron``/``condition``/``actions`` 任一时,经
        ``update_workflow_spec`` 部分更新并回传刷新后的 workflow 行。
        """
        from hermes_cloud.app.crm.workflows import (
            list_workflows, set_workflow_enabled, set_workflow_status,
            update_workflow_spec,
        )
        out: dict = {"ok": True, "id": workflow_id}
        if "status" in payload:
            try:
                ok = set_workflow_status(db, workflow_id, str(payload.get("status")))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            if not ok:
                raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not found")
            out["status"] = str(payload.get("status")).strip().lower()
        if "enabled" in payload:
            enabled = bool(payload.get("enabled"))
            ok = set_workflow_enabled(db, workflow_id, enabled)
            if not ok:
                raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not found")
            out["enabled"] = enabled
        # 结构化局部更新(只取 spec 键,避免把 status/enabled 误传进去)。
        spec_keys = ("name", "trigger_on", "schedule_cron", "condition", "actions")
        patch = {k: payload[k] for k in spec_keys if k in payload}
        if patch:
            try:
                ok = update_workflow_spec(db, workflow_id, patch)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            if not ok:
                raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not found")
            # 回传刷新后的完整行(供前端就地刷新块编辑器)。
            fresh = next(
                (w for w in list_workflows(db) if int(w.get("id")) == int(workflow_id)),
                None,
            )
            if fresh is not None:
                out["workflow"] = fresh
        return out

    @router.delete("/workflows/{workflow_id}")
    def delete_workflow_route(
        workflow_id: int,
        _user: dict = Depends(_require_admin),  # 仅管理员可删
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """删除(软删)一条工作流。删后从列表消失、不再触发,可后台恢复。"""
        from hermes_cloud.app.crm.workflows import delete_workflow
        if not delete_workflow(db, workflow_id):
            raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not found")
        return {"ok": True, "id": workflow_id}

    @router.post("/workflows/{workflow_id}/test-run")
    def test_run_workflow(
        workflow_id: int,
        payload: dict,
        _user: dict = Depends(_require_admin),  # 试跑会真写记录,仅管理员
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """试跑一条工作流(供 draft 反复验证)。body: ``{"record_id": <int>}``。

        ``force=True`` 绕过 status='live' 闸 + 绕过去重,可对同记录反复触发。同步执行
        并回传 run 结果 dict({status, run_id, results, ...}),便于前端即时看效果。
        """
        rid = payload.get("record_id")
        from hermes_cloud.app.crm.workflows import run_workflow
        result = run_workflow(
            int(workflow_id),
            None if rid is None else int(rid),
            "test",
            force=True,
        )
        return {"ok": True, "id": workflow_id, "result": result}

    @router.post("/workflows/runs/{run_id}/undo")
    def undo_run(
        run_id: int,
        payload: dict,
        _user: dict = Depends(_require_admin),  # 撤销带权限 bypass 写/删,仅管理员
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """撤销一次 run 的可逆动作(revert_field / delete_row)。webhook 等不可逆动作
        进 skipped。body 可空。返回 ``{ok, run_id, undone, skipped, failed}``。
        404=run 不存在;409=已撤销或状态不可撤(detail 携 reason)。
        """
        from hermes_cloud.app.crm.workflows import undo_workflow_run
        res = undo_workflow_run(db, int(run_id))
        st = res.pop("_status", None)
        if st == 404:
            raise HTTPException(status_code=404, detail=res.get("reason") or "run not found")
        if st == 409:
            raise HTTPException(status_code=409, detail=res)
        return res

    # ---- 审批链 REST(approval chain)----
    # body 一律用 dict(future-annotations 下局部 Pydantic 模型会被误判 query)。
    # 审批状态真相在 approvals 表;引擎方法已在 test_crm_approval_flow 充分覆盖。

    @router.post("/approvals/submit")
    def submit_approval(
        payload: dict,
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """把记录提交进审批流。未配链 → skipped=no_config;已在审 → already_pending。"""
        from hermes.crm.approval.engine import ApprovalEngine

        mod = _resolve_module(db, str(payload.get("module") or ""))
        res = ApprovalEngine(db).submit_for_record(
            int(mod["id"]), int(payload.get("record_id") or 0), int(user["id"])
        )
        # 提交成功 → best-effort 给审批人推渠道通知(失败不阻断提交)。
        if res.get("submitted"):
            try:
                from hermes_cloud.app.crm.approval_notify import notify_approvers
                notify_approvers(db, int(res["approval_id"]))
            except Exception:
                pass
        return res

    @router.get("/approvals/pending")
    def pending_approvals(
        user: dict = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """当前用户的待审批列表(轮到我审批的 approval 实例)。"""
        from hermes.crm.approval.engine import ApprovalEngine

        rows = ApprovalEngine(db).pending_for_user(int(user["id"]))
        return {"items": rows, "count": len(rows)}

    def _approval_action(db, user, approval_id, fn, payload):
        from hermes.crm.approval.engine import ApprovalEngine

        eng = ApprovalEngine(db)
        try:
            if fn == "approve":
                return eng.approve(approval_id, int(user["id"]), str(payload.get("remark") or ""))
            if fn == "reject":
                return eng.reject(approval_id, int(user["id"]), str(payload.get("remark") or ""))
            if fn == "back":
                return eng.back_to_step(
                    approval_id, int(user["id"]),
                    int(payload.get("target_step") or 0), str(payload.get("remark") or ""),
                )
            return eng.transfer(
                approval_id, int(payload.get("from_user_id") or 0),
                int(payload.get("to_user_id") or 0),
            )
        except PermissionError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @router.post("/approvals/{approval_id}/approve")
    def approve_approval(
        approval_id: int, payload: dict,
        user: dict = Depends(_require_user), db: DbWrapper = Depends(get_db),
    ) -> dict:
        return _approval_action(db, user, approval_id, "approve", payload)

    @router.post("/approvals/{approval_id}/reject")
    def reject_approval(
        approval_id: int, payload: dict,
        user: dict = Depends(_require_user), db: DbWrapper = Depends(get_db),
    ) -> dict:
        return _approval_action(db, user, approval_id, "reject", payload)

    @router.post("/approvals/{approval_id}/back")
    def back_approval(
        approval_id: int, payload: dict,
        user: dict = Depends(_require_user), db: DbWrapper = Depends(get_db),
    ) -> dict:
        return _approval_action(db, user, approval_id, "back", payload)

    @router.post("/approvals/{approval_id}/transfer")
    def transfer_approval(
        approval_id: int, payload: dict,
        user: dict = Depends(_require_user), db: DbWrapper = Depends(get_db),
    ) -> dict:
        return _approval_action(db, user, approval_id, "transfer", payload)

    @router.get("/modules/{module_name}/approval-config")
    def get_approval_config(
        module_name: str,
        _admin: dict = Depends(_require_admin), db: DbWrapper = Depends(get_db),
    ) -> dict:
        from hermes.crm.approval.engine import ApprovalEngine

        mod = _resolve_module(db, module_name)
        return {"config": ApprovalEngine(db).get_config(int(mod["id"]))}

    @router.put("/modules/{module_name}/approval-config")
    def put_approval_config(
        module_name: str, payload: dict,
        _admin: dict = Depends(_require_admin), db: DbWrapper = Depends(get_db),
    ) -> dict:
        from hermes.crm.approval.engine import ApprovalEngine

        mod = _resolve_module(db, module_name)
        steps = payload.get("steps") or []
        if not isinstance(steps, list) or not steps:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="steps must be a non-empty array",
            )
        ApprovalEngine(db).save_config(
            int(mod["id"]), steps, int(payload.get("allow_edit_after_approve") or 0)
        )
        return {"ok": True}

    @router.get("/modules/{module_name}/writeback-targets")
    def writeback_targets(
        module_name: str,
        _admin: dict = Depends(_require_admin),
        db: DbWrapper = Depends(get_db),
    ) -> dict:
        """本模块通过 ``{目标}_id`` 外键关联到的模块 + 各自数值字段。

        跨模块汇总规则的 target 候选。引擎按 ``{target_table}_id`` 找目标记录,
        故只取列名正好 ``{target}_id`` 的主关联(排除 ``accounts_id_2`` 这类
        次级关联——列名 ≠ ``{target}_id``,引擎不认)。
        """
        _NUMERIC = ["number", "currency", "float", "int", "percent", "formula"]
        mod = _resolve_module(db, module_name)
        rels = db.fetchall(
            "SELECT field_name FROM module_fields "
            "WHERE module_id = %s AND field_type = 'relation' AND field_name ~ '_id$'",
            (int(mod["id"]),),
        ) or []
        targets = []
        for r in rels:
            fk = str(r["field_name"])
            tbl = fk[:-3]  # 去掉结尾 '_id'
            tmod = db.fetchone(
                "SELECT id, name, label FROM modules WHERE table_name = %s", (tbl,)
            )
            if not tmod:
                continue
            nums = db.fetchall(
                "SELECT field_name, field_label FROM module_fields "
                "WHERE module_id = %s AND field_type = ANY(%s)",
                (int(tmod["id"]), _NUMERIC),
            ) or []
            targets.append({
                "module": tmod["name"],
                "label": tmod.get("label"),
                "fk_field": fk,
                "fields": [
                    {"field_name": n["field_name"], "field_label": n.get("field_label")}
                    for n in nums
                ],
            })
        return {"targets": targets}

    return router
