"""FastAPI routes for /api/dashboards/*.

CRUD on tabs/charts + chart data (cached 60s) + drill-to-detail +
streaming chat (SSE). Auth pattern mirrors decisions/routes.py
(bearer token first, bridge identity fallback).

Mounted via register_routes(app) called from hermes_cloud.app.main.
"""
from __future__ import annotations

import json
import logging
import pathlib
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from hermes.crm.db import DbWrapper, get_db

from hermes_cloud.app.scope.audit import record_scope_access

from . import service, store
from .compiler import (
    CompileError,
    compile_drill,
    compile_dsl,
)
from .dsl import DSLQuery
from .grants import grants_for_user
from .ops import OpError, apply_ops
from .presets import presets_for_role
from .runtime_schema import _NORMALIZATION, refresh_runtime_schema
from .scope import (
    ScopeError,
    default_scope_for_role,
    resolve_scope,
)
from .semantic.registry import Registry


logger = logging.getLogger(__name__)


# ---- registry: curated baseline + runtime extension -----------------------
#
# Two layers:
#   * _BASE_REGISTRY: loaded once from YAML files (immutable, curated).
#   * _ACTIVE_REGISTRY: BASE + custom fields discovered from module_fields,
#                       refreshed every _REFRESH_TTL seconds (or on demand
#                       via /api/dashboards/_admin/refresh_schema).
# Custom fields appear in `module_fields` whenever an admin uses the
# field-builder; without this layer the dashboards layer would never see them.

_BASE_REGISTRY: Optional[Registry] = None
_ACTIVE_REGISTRY: Optional[Registry] = None
_ACTIVE_REGISTRY_TS: float = 0.0
_REFRESH_TTL = 60.0  # seconds


def _base_registry() -> Registry:
    global _BASE_REGISTRY
    if _BASE_REGISTRY is None:
        _BASE_REGISTRY = Registry.from_directory(
            pathlib.Path(__file__).parent / "semantic"
        )
    return _BASE_REGISTRY


def _registry(db=None) -> Registry:
    """Return the active (custom-field-aware) registry.

    Refreshes from DB at most every _REFRESH_TTL seconds. If db is None
    or refresh fails, falls back to the base or last-cached registry.
    """
    global _ACTIVE_REGISTRY, _ACTIVE_REGISTRY_TS
    base = _base_registry()
    if db is None:
        return _ACTIVE_REGISTRY or base
    if _ACTIVE_REGISTRY is None or (time.time() - _ACTIVE_REGISTRY_TS) > _REFRESH_TTL:
        try:
            _ACTIVE_REGISTRY = refresh_runtime_schema(db, base)
            _ACTIVE_REGISTRY_TS = time.time()
        except Exception:
            logger.exception("dashboards: runtime schema refresh failed")
            _ACTIVE_REGISTRY = _ACTIVE_REGISTRY or base
    return _ACTIVE_REGISTRY


def force_refresh_registry(db) -> None:
    """Invalidate cache + force immediate rebuild. Called from the
    field-builder hook so admins see new fields without waiting for TTL.
    """
    global _ACTIVE_REGISTRY, _ACTIVE_REGISTRY_TS
    _ACTIVE_REGISTRY_TS = 0.0
    _registry(db)


# ---- auth helper (mirrors decisions) -------------------------------------


def _user_id_from_request(request: Request, app: Any) -> int:
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


# ---- user context lookup -------------------------------------------------


def _user_context(db: DbWrapper, uid: int) -> Dict[str, Any]:
    """Resolve role + dept + grants for a user.

    Role determined per service.resolve_role:
      - is_admin -> admin
      - has_reports (someone reports to caller) -> manager
      - else -> employee
    """
    row = db.fetchone(
        "SELECT id, is_admin, department_id FROM users WHERE id = %s",
        (uid,),
    ) or {}
    has_reports_row = db.fetchone(
        "SELECT 1 FROM users WHERE deleted = FALSE AND report_to_ids @> to_jsonb(%s::int) LIMIT 1",
        (uid,),
    )
    is_admin = bool(row.get("is_admin"))
    has_reports = has_reports_row is not None
    role = service.resolve_role(is_admin=is_admin, has_reports=has_reports)
    grants = grants_for_user(db, user_id=uid, role=role)
    return {
        "uid": uid,
        "role": role,
        "department_id": row.get("department_id"),
        "grants": grants,
        "is_admin": is_admin,
    }


# Well-known FK columns → referenced entity name. Used to resolve drill
# detail rows (where columns aren't tagged as type='ref' because they're
# just raw detail_columns from each entity yaml). Aligned with the kacrm
# column naming convention.
_DRILL_COL_REFS: Dict[str, str] = {
    "owner_user_id":  "user",
    "actor_user_id":  "user",
    "created_by":     "user",
    "updated_by":     "user",
    "approval_user_id": "user",
    "convertedby":    "user",
    "agent_id":       "user",
    "department_id":  "department",
    "parent_id":      "department",
    "accounts_id":    "customer",
    "contacts_id":    "contact",
    "leads_id":       "lead",
    "potentials_id":  "opportunity",
    "tab_id":         None,   # not resolved
}
_DRILL_COL_REFS = {k: v for k, v in _DRILL_COL_REFS.items() if v}


# ---- chart data cache (in-process, 60s) ----------------------------------

_CACHE_TTL = 60.0
_CACHE: Dict[tuple, tuple[float, Any]] = {}


def _cache_get(key: tuple) -> Optional[Any]:
    rec = _CACHE.get(key)
    if rec is None:
        return None
    ts, val = rec
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: tuple, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


# ---- request bodies ------------------------------------------------------


class TabCreateBody(BaseModel):
    title: str


class TabPatchBody(BaseModel):
    title: Optional[str] = None
    visibility: Optional[str] = None
    shared_with: Optional[Dict[str, Any]] = None
    position: Optional[int] = None



class DrillBody(BaseModel):
    dim_filters: Dict[str, Any] = {}
    extra_columns: List[str] = []
    limit: int = 1000


class ChartPatchBody(BaseModel):
    title: Optional[str] = None
    grid_w: Optional[int] = None
    grid_h: Optional[int] = None
    dsl: Optional[Dict[str, Any]] = None


# ---- chart data execution helper -----------------------------------------


def _run_chart_query(
    db: DbWrapper, *,
    chart: store.DashboardChart,
    ctx: Dict[str, Any],
    requested_scope: Optional[str],
) -> Dict[str, Any]:
    # Text widgets have no SQL backing — return the inline body as-is so
    # the frontend can render it without round-tripping through the
    # semantic-layer compiler. We still publish chart_spec so the FE
    # picks the "text" rendering branch.
    chart_spec = (chart.dsl or {}).get("chart") or {}
    if (chart_spec.get("type") or "").lower() == "text":
        return {
            "columns": [],
            "rows": [],
            "column_labels": {},
            "chart_spec": {
                "type": "text",
                "title": chart_spec.get("title") or chart.title,
                "text": chart_spec.get("text") or "",
                "x": None, "y": None, "y2": None, "series": None,
            },
            "scope_resolved": "n/a",
        }

    dsl = DSLQuery(**chart.dsl)
    try:
        scope = resolve_scope(
            role=ctx["role"],
            requested=requested_scope or dsl.scope,
            grants=ctx["grants"],
        )
    except ScopeError:
        # auto-degrade to default for caller's role (spec §5.e)
        scope = default_scope_for_role(ctx["role"])
    dsl = dsl.model_copy(update={"scope": scope})

    try:
        compiled = compile_dsl(
            dsl, registry=_registry(db),
            caller_id=ctx["uid"], grants=ctx["grants"],
        )
    except CompileError as e:
        # measure-grant or other compile failure → 403 with friendly message
        raise HTTPException(
            status_code=403,
            detail={"error": "compile_error", "message": str(e)},
        )
    rows = db.fetchall(compiled.sql, compiled.params)
    out_rows = [[r.get(c) for c in compiled.columns] for r in rows]

    # Build a per-column label map for ref-typed dimensions so the chart
    # can render "销售部" while still keeping the raw FK ("2") in the row
    # data — drill clicks send the raw value back to the backend, which
    # the SQL needs as an integer not a name.
    column_labels: Dict[str, Dict[str, str]] = {}
    dimension_orders: Dict[str, List[str]] = {}
    registry = _registry(db)
    entity = registry.entity(dsl.entity)
    for d_dsl in dsl.dimensions:
        try:
            dim = entity.dimension(d_dsl.name)
        except KeyError:
            continue
        if dim.type == "ref" and dim.references:
            try:
                col_idx = compiled.columns.index(dim.name)
            except ValueError:
                continue
            ids = {row[col_idx] for row in out_rows if row[col_idx] is not None}
            if not ids:
                continue
            labels = _resolve_ref_labels(db, registry, dim.references, ids)
            if labels:
                # JSON keys must be strings; client maps via String(rawValue).
                column_labels[dim.name] = {str(k): v for k, v in labels.items()}
            continue

        # Picklist dimensions: pull options + per-locale labels from
        # module_fields so the chart shows what admins configured under
        # Schema Hub (CSV order + ``field_config.option_labels``), not
        # the static frontend dictionary. Authoritative source — the
        # ``/options/order`` PATCH route writes the same CSV.
        opts = _picklist_options_for_dim(db, entity, dim.name)
        if opts:
            # value -> localized text. Only override for values that
            # have a non-empty resolved label; otherwise let the FE
            # fallback (raw / static dict) take over.
            labels = {
                str(o["value"]): str(o["text"])
                for o in opts
                if str(o.get("text") or "").strip()
            }
            if labels:
                column_labels[dim.name] = labels
            dimension_orders[dim.name] = [str(o["value"]) for o in opts]

    # For "stage-meaningful" chart types, sort rows by the configured
    # option order so the funnel / composite / table reads in the order
    # admins set under Schema Hub, regardless of what the DSL's
    # ``order_by`` clause produced (typically count desc). Other chart
    # types keep DSL order so top-N pies/bars are unaffected.
    chart_type = (dsl.chart.type or "").lower()
    if chart_type in {"funnel", "composite", "table", "report_table"}:
        x_col = dsl.chart.x
        order = dimension_orders.get(x_col) if x_col else None
        if order and x_col in compiled.columns:
            x_idx = compiled.columns.index(x_col)
            pos = {v: i for i, v in enumerate(order)}
            # Unknown / NULL values sort to the end, preserving their
            # incoming relative order via a stable sort.
            tail = len(order)
            out_rows.sort(key=lambda r: pos.get(
                str(r[x_idx]) if r[x_idx] is not None else "", tail,
            ))

    return {
        "columns": compiled.columns,
        "rows": out_rows,
        "column_labels": column_labels,
        "dimension_orders": dimension_orders,
        "chart_spec": dsl.chart.model_dump(),
        "scope_resolved": scope,
    }


# Cache picklist metadata for 60s — same TTL as chart data. Keyed by
# (module_table, field_name); value is the localized option list for
# the active request locale (cached per-locale automatically since the
# key includes the locale).
_PICKLIST_CACHE: Dict[tuple, tuple[float, list]] = {}
_PICKLIST_TTL = 60.0


def _picklist_options_for_dim(
    db: DbWrapper, entity, dim_name: str,
) -> list:
    """Return localized ``[{value,text,...}]`` options for a picklist dim.

    Resolves the underlying ``module_fields`` row by:
      1. Direct match: ``field_name == dim_name`` (covers the common
         case where dim names mirror table columns).
      2. Reverse ``_NORMALIZATION`` lookup: if the dim is an alias
         (e.g. opportunity ``status`` → real col ``milestone``), look
         up the real column. The dashboard view exposes both names but
         ``module_fields.field_name`` only carries the real one.

    Returns ``[]`` when the entity has no ``module_table``, the field
    isn't picklist-typed, or the options CSV is empty. Caller treats
    empty as "no metadata; skip label/order injection for this dim".
    """
    table = entity.module_table
    if not table:
        return []

    # Resolve dim → real module_fields.field_name via _NORMALIZATION
    # reverse map. ``norm`` is ``{real_col: alias}``; we want the alias
    # → real_col direction.
    norm = _NORMALIZATION.get(entity.name, {})
    alias_to_real = {alias: real for real, alias in norm.items()}
    candidates = [dim_name]
    if dim_name in alias_to_real:
        candidates.append(alias_to_real[dim_name])

    try:
        from hermes.crm.i18n.options import (
            localize_picklist_options,
            parse_picklist_options,
        )
        from hermes.crm.i18n.service import get_current_locale
    except Exception:
        return []

    locale = ""
    try:
        locale = get_current_locale() or ""
    except Exception:
        locale = ""

    cache_key = (table, dim_name, locale)
    now = time.time()
    cached = _PICKLIST_CACHE.get(cache_key)
    if cached and now - cached[0] < _PICKLIST_TTL:
        return cached[1]

    row = None
    for field_name in candidates:
        row = db.fetchone(
            """
            SELECT mf.field_type, mf.options, mf.field_config
              FROM module_fields mf
              JOIN collections c ON c.id = mf.module_id
             WHERE c.name = %s AND mf.field_name = %s
             LIMIT 1
            """,
            (table, field_name),
        )
        if row:
            break
    if not row:
        _PICKLIST_CACHE[cache_key] = (now, [])
        return []

    ftype = str(row.get("field_type") or "").lower()
    if ftype not in ("select", "radio", "checkbox", "multiselect", "picklist"):
        _PICKLIST_CACHE[cache_key] = (now, [])
        return []

    parsed = parse_picklist_options(row.get("options"))
    if not parsed:
        _PICKLIST_CACHE[cache_key] = (now, [])
        return []
    localized = localize_picklist_options(
        parsed, locale=locale or None,
        field_config=row.get("field_config"),
    )
    _PICKLIST_CACHE[cache_key] = (now, localized)
    return localized


# Cache name lookups for 60s — same TTL as chart data.
_LABEL_CACHE: Dict[tuple, tuple[float, Dict[Any, str]]] = {}


def _resolve_ref_labels(db: DbWrapper, registry, ref_entity_name: str,
                         ids: set) -> Dict[Any, str]:
    """Map FK ids -> display labels for a referenced entity.

    Picks the best 'name' column from the entity's detail_columns.default:
    prefers ``name``/``realname`` over ``username``; falls back to
    ``str(id)`` if no obvious name column exists.
    """
    if not ids:
        return {}
    try:
        ref_entity = registry.entity(ref_entity_name)
    except KeyError:
        return {}

    # Prefer a real name; fall back to first non-id column.
    default_cols = list(ref_entity.detail_columns.default)
    name_col = None
    for candidate in ("name", "realname", "username", "title"):
        if candidate in default_cols:
            name_col = candidate
            break
    if name_col is None:
        non_id = [c for c in default_cols if c != "id"]
        if not non_id:
            return {}
        name_col = non_id[0]

    cache_key = (ref_entity_name, name_col, frozenset(ids))
    cached = _LABEL_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        rows = db.fetchall(
            f"SELECT id, {name_col} AS _label FROM {ref_entity.source_view} "
            f"WHERE id = ANY(%s)",
            (list(ids),),
        )
    except Exception as exc:
        logger.warning("ref label lookup failed for %s: %s", ref_entity_name, exc)
        return {}

    out: Dict[Any, str] = {}
    for r in rows:
        label = r.get("_label")
        out[r["id"]] = str(label) if label is not None else f"#{r['id']}"

    # IDs the underlying view doesn't expose (deleted/orphan FKs, or
    # rows the caller can't see) still need a sensible label or the
    # chart shows a meaningless raw int. Use a "#<id>" placeholder
    # plus an entity hint so the user knows it's an unresolved FK.
    entity_hint = _UNRESOLVED_REF_HINT.get(ref_entity_name, "")
    for raw_id in ids:
        if raw_id not in out:
            out[raw_id] = f"{entity_hint}#{raw_id}" if entity_hint else f"#{raw_id}"

    _LABEL_CACHE[cache_key] = (time.time(), out)
    return out


# Human-readable hint shown when a ref FK can't be resolved (deleted
# user, missing department row, etc). Kept terse — locale-specific
# polishing happens client-side via i18n if needed.
_UNRESOLVED_REF_HINT: Dict[str, str] = {
    "user":       "用户",
    "department": "部门",
    "customer":   "客户",
    "contact":    "联系人",
    "lead":       "线索",
    "opportunity": "商机",
}


# ---- preset materialization ---------------------------------------------


_TAB_TITLE_FALLBACK_LOCALES: tuple[str, ...] = ("zh-CN", "en-US")


def _resolved_tab_title(t: store.DashboardTab) -> str:
    """Pick the right display title for the current request locale.

    Resolution: ``title_i18n[<current_locale>]`` → other configured
    locales in fallback order → raw ``title``. User-renamed tabs have
    ``title_i18n=None`` (cleared by ``patch_tab_route``) and short-
    circuit straight to ``title``, so a user-typed name never gets
    silently overridden when they switch language.
    """
    if not t.title_i18n:
        return t.title
    try:
        from hermes.crm.i18n.service import get_current_locale
        locale = get_current_locale() or ""
    except Exception:
        locale = ""
    if locale and t.title_i18n.get(locale):
        return t.title_i18n[locale]
    for fb in _TAB_TITLE_FALLBACK_LOCALES:
        if t.title_i18n.get(fb):
            return t.title_i18n[fb]
    return t.title


def _materialize_presets(db: DbWrapper, uid: int, role: str) -> None:
    """First-visit lazy materialization (spec §9.d)."""
    for pos, preset in enumerate(presets_for_role(role)):
        new_tab = store.create_tab(
            db, owner_user_id=uid, title=preset.title, position=pos,
            is_system_preset=True, preset_key=preset.key,
            title_i18n=dict(preset.title_i18n),
        )
        for cpos, ch in enumerate(preset.charts):
            store.create_chart(
                db, tab_id=new_tab.id, title=ch.title,
                dsl=ch.dsl, position=cpos,
            )


# ---- router registration ------------------------------------------------


def register_routes(app: Any) -> None:
    router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])

    # ----- TABS -----------------------------------------------------------

    @router.get("/tabs")
    def list_tabs_route(
        request: Request, db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        rows = store.list_tabs_for_user(db, user_id=uid)
        if not rows:
            ctx = _user_context(db, uid)
            _materialize_presets(db, uid, ctx["role"])
            rows = store.list_tabs_for_user(db, user_id=uid)
        return {
            "tabs": [
                {
                    "id": str(r.id), "owner_user_id": r.owner_user_id,
                    "title": _resolved_tab_title(r),
                    "position": r.position,
                    "visibility": r.visibility, "shared_with": r.shared_with,
                    "is_system_preset": r.is_system_preset,
                    "preset_key": r.preset_key,
                }
                for r in rows
            ],
        }

    @router.post("/tabs")
    def create_tab_route(
        body: TabCreateBody, request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        t = store.create_tab(db, owner_user_id=uid, title=body.title)
        return {
            "id": str(t.id), "owner_user_id": t.owner_user_id,
            "title": t.title, "position": t.position,
            "visibility": t.visibility, "shared_with": t.shared_with,
            "is_system_preset": t.is_system_preset, "preset_key": t.preset_key,
        }

    @router.get("/tabs/{tab_id}")
    def get_tab_route(
        tab_id: str, request: Request, db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        t = store.get_tab(db, tab_id)
        if not t:
            raise HTTPException(404)
        ctx = _user_context(db, uid)
        if not service.can_view_tab(t, viewer_id=uid,
                                    viewer_dept_id=ctx["department_id"]):
            raise HTTPException(404)  # do not leak existence
        charts = store.list_charts(db, tab_id=t.id)
        return {
            "tab": {
                "id": str(t.id), "owner_user_id": t.owner_user_id,
                "title": _resolved_tab_title(t),
                "position": t.position,
                "visibility": t.visibility, "shared_with": t.shared_with,
                "is_system_preset": t.is_system_preset,
                "preset_key": t.preset_key,
            },
            "charts": [
                {"id": str(c.id), "tab_id": str(c.tab_id),
                 "position": c.position, "title": c.title, "dsl": c.dsl,
                 "grid_w": c.grid_w, "grid_h": c.grid_h}
                for c in charts
            ],
        }

    @router.patch("/tabs/{tab_id}")
    def patch_tab_route(
        tab_id: str, body: TabPatchBody, request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        t = store.get_tab(db, tab_id)
        if not t or not service.can_edit_tab(t, viewer_id=uid):
            raise HTTPException(404)
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        # User rename of a preset tab: drop the bilingual override so
        # their chosen text always wins, regardless of which locale
        # they're using when they switch later. The patch body never
        # exposes ``title_i18n`` directly — only the raw ``title`` —
        # so a user can't accidentally desync the two.
        if "title" in fields:
            fields["title_i18n"] = None
        upd = store.update_tab(db, tab_id, **fields)
        return {"id": str(upd.id), "title": _resolved_tab_title(upd),
                "visibility": upd.visibility,
                "shared_with": upd.shared_with,
                "position": upd.position}

    @router.delete("/tabs/{tab_id}")
    def delete_tab_route(
        tab_id: str, request: Request, db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        t = store.get_tab(db, tab_id)
        if not t or not service.can_edit_tab(t, viewer_id=uid):
            raise HTTPException(404)
        store.delete_tab(db, tab_id)
        return {"ok": True}

    @router.post("/tabs/{tab_id}/fork")
    def fork_tab_route(
        tab_id: str, request: Request, db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        src = store.get_tab(db, tab_id)
        if not src:
            raise HTTPException(404)
        ctx = _user_context(db, uid)
        if not service.can_view_tab(src, viewer_id=uid,
                                    viewer_dept_id=ctx["department_id"]):
            raise HTTPException(404)
        new = service.fork_tab(db, source_tab_id=tab_id, new_owner_id=uid)
        return {"id": str(new.id), "title": new.title}

    # ----- CHART DATA -----------------------------------------------------

    @router.get("/charts/{chart_id}/data")
    def chart_data_route(
        chart_id: str, request: Request,
        scope: Optional[str] = Query(None),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        ch = store.get_chart(db, chart_id)
        if not ch:
            raise HTTPException(404)
        parent = store.get_tab(db, ch.tab_id)
        ctx = _user_context(db, uid)
        if not service.can_view_tab(parent, viewer_id=uid,
                                    viewer_dept_id=ctx["department_id"]):
            raise HTTPException(404)
        # Include locale in the cache key — chart payload carries
        # locale-resolved picklist labels (``column_labels``), so a
        # cache that ignores locale would serve zh-CN users en-US
        # labels (or vice-versa) after the first hit pinned the entry.
        try:
            from hermes.crm.i18n.service import get_current_locale
            _loc = get_current_locale()
        except Exception:
            _loc = ""
        cache_key = (chart_id, uid, scope or "", _loc)
        hit = _cache_get(cache_key)
        if hit is not None:
            return hit
        out = _run_chart_query(db, chart=ch, ctx=ctx, requested_scope=scope)
        _cache_put(cache_key, out)
        return out

    @router.post("/charts/{chart_id}/refresh")
    def chart_refresh_route(
        chart_id: str, request: Request,
        scope: Optional[str] = Query(None),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        # Include locale in the cache key — chart payload carries
        # locale-resolved picklist labels (``column_labels``), so a
        # cache that ignores locale would serve zh-CN users en-US
        # labels (or vice-versa) after the first hit pinned the entry.
        try:
            from hermes.crm.i18n.service import get_current_locale
            _loc = get_current_locale()
        except Exception:
            _loc = ""
        cache_key = (chart_id, uid, scope or "", _loc)
        _CACHE.pop(cache_key, None)
        return chart_data_route(chart_id, request, scope=scope, db=db)

    @router.delete("/charts/{chart_id}")
    def delete_chart_route(
        chart_id: str, request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        ch = store.get_chart(db, chart_id)
        if not ch:
            raise HTTPException(404)
        parent = store.get_tab(db, ch.tab_id)
        if not parent or not service.can_edit_tab(parent, viewer_id=uid):
            raise HTTPException(404)
        store.delete_chart(db, ch.id)
        # Invalidate cache so subsequent visits don't try to render a
        # deleted chart's data.
        for k in [k for k in _CACHE if k[0] == chart_id]:
            _CACHE.pop(k, None)
        return {"ok": True}

    @router.patch("/charts/{chart_id}")
    def patch_chart_route(
        chart_id: str, body: ChartPatchBody, request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        ch = store.get_chart(db, chart_id)
        if not ch:
            raise HTTPException(404)
        parent = store.get_tab(db, ch.tab_id)
        if not parent or not service.can_edit_tab(parent, viewer_id=uid):
            raise HTTPException(404)
        fields: Dict[str, Any] = {}
        if body.title is not None:
            fields["title"] = body.title
        if body.grid_w is not None:
            fields["grid_w"] = max(1, min(4, int(body.grid_w)))
        if body.grid_h is not None:
            fields["grid_h"] = max(1, min(4, int(body.grid_h)))
        if body.dsl is not None:
            fields["dsl"] = body.dsl
        if not fields:
            return {"ok": True, "noop": True}
        upd = store.update_chart(db, ch.id, **fields)
        # Bust the cache when DSL changed — grid-only edits don't affect
        # the query result but DSL edits do.
        if "dsl" in fields:
            for k in [k for k in _CACHE if k[0] == chart_id]:
                _CACHE.pop(k, None)
        return {
            "ok": True,
            "id": str(upd.id), "title": upd.title,
            "grid_w": upd.grid_w, "grid_h": upd.grid_h,
        }

    # ----- DRILL ----------------------------------------------------------

    @router.post("/charts/{chart_id}/drill")
    def chart_drill_route(
        chart_id: str, body: DrillBody, request: Request,
        scope: Optional[str] = Query(None),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        uid = _user_id_from_request(request, app)
        ch = store.get_chart(db, chart_id)
        if not ch:
            raise HTTPException(404)
        parent = store.get_tab(db, ch.tab_id)
        ctx = _user_context(db, uid)
        if not service.can_view_tab(parent, viewer_id=uid,
                                    viewer_dept_id=ctx["department_id"]):
            raise HTTPException(404)
        dsl = DSLQuery(**ch.dsl)
        try:
            resolved = resolve_scope(
                role=ctx["role"],
                requested=scope or dsl.scope,
                grants=ctx["grants"],
            )
        except ScopeError:
            resolved = default_scope_for_role(ctx["role"])
        try:
            # Carry over the parent chart's per-dim granularity so a
            # drill on a time-binned chart (e.g. occurred_at by week)
            # compares date_trunc-style buckets instead of raw timestamps.
            dim_grans = {
                d.name: d.granularity for d in dsl.dimensions if d.granularity
            }
            compiled = compile_drill(
                entity_name=dsl.entity, scope=resolved, caller_id=uid,
                dim_filters=body.dim_filters, extra_columns=body.extra_columns,
                registry=_registry(db), grants=ctx["grants"], limit=body.limit,
                dim_granularities=dim_grans,
            )
        except CompileError as exc:
            msg = str(exc)
            if "grant" in msg:
                raise HTTPException(403, msg)
            if "not in optional" in msg or "not in" in msg:
                raise HTTPException(400, msg)
            raise HTTPException(400, msg)
        rows = db.fetchall(compiled.sql, compiled.params)
        out_rows = [[r.get(c) for c in compiled.columns] for r in rows]

        # Resolve FK detail columns (owner_user_id, department_id,
        # accounts_id, ...) to display names. Drill columns aren't
        # Dimension objects (no type=ref tag), so we use a static
        # name→entity map of well-known kacrm FK conventions.
        registry = _registry(db)
        column_labels: Dict[str, Dict[str, str]] = {}
        for col in compiled.columns:
            ref_entity = _DRILL_COL_REFS.get(col)
            if not ref_entity:
                continue
            try:
                col_idx = compiled.columns.index(col)
            except ValueError:
                continue
            ids = {row[col_idx] for row in out_rows if row[col_idx] is not None}
            if not ids:
                continue
            labels = _resolve_ref_labels(db, registry, ref_entity, ids)
            if labels:
                column_labels[col] = {str(k): v for k, v in labels.items()}

        return {
            "columns": compiled.columns,
            "rows": out_rows,
            "column_labels": column_labels,
        }

    @router.get("/charts/{chart_id}/drill/export")
    def chart_drill_export_route(
        chart_id: str, request: Request,
        format: str = Query("csv"),
        db: DbWrapper = Depends(get_db),
    ):
        if format != "csv":
            raise HTTPException(400, "format must be csv")
        # delegate to drill with empty body (returns full default rows)
        data = chart_drill_route(chart_id, DrillBody(), request, db=db)
        # Audit every export — drill route already enforced ACL + scope,
        # so by this point uid/chart/scope are validated. Re-resolving
        # here keeps the audit row self-describing.
        uid = _user_id_from_request(request, app)
        ch = store.get_chart(db, chart_id)
        entity = None
        resolved = None
        if ch is not None:
            try:
                dsl = DSLQuery(**ch.dsl)
                entity = dsl.entity
                ctx = _user_context(db, uid)
                try:
                    resolved = resolve_scope(
                        role=ctx["role"],
                        requested=dsl.scope,
                        grants=ctx["grants"],
                    )
                except ScopeError:
                    resolved = default_scope_for_role(ctx["role"])
            except Exception:
                logger.warning("drill export audit: failed to resolve scope/entity for chart=%s", chart_id, exc_info=True)
        # audit_log CHECK constraint allows scope_kind in {self,team,org}
        # only — fallback to 'self' on the rare refetch-failed path so
        # the audit row still lands instead of silently failing the CHECK.
        record_scope_access(
            db,
            caller_id=uid,
            scope_kind=resolved or "self",
            resource_kind="dashboard_drill_export",
            resource_filter={
                "chart_id": chart_id,
                "entity": entity,
                "format": "csv",
                "row_count": len(data["rows"]),
                "column_count": len(data["columns"]),
            },
        )
        import csv
        from io import StringIO
        buf = StringIO()
        w = csv.writer(buf)
        w.writerow(data["columns"])
        for row in data["rows"]:
            w.writerow(row)
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="drill_{chart_id}.csv"',
            },
        )

    @router.get("/export-audit")
    def export_audit_route(
        request: Request,
        caller_id: Optional[int] = Query(None),
        since: Optional[str] = Query(None, description="ISO datetime, inclusive"),
        until: Optional[str] = Query(None, description="ISO datetime, exclusive"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        """Admin-only: query dashboard CSV export audit trail.

        Returns rows from ``audit_log`` filtered to
        ``resource_kind='dashboard_drill_export'`` with the caller's
        display name JOINed in for readability.
        """
        uid = _user_id_from_request(request, app)
        ctx = _user_context(db, uid)
        if not ctx["is_admin"]:
            raise HTTPException(403, "admin only")

        where = ["a.resource_kind = 'dashboard_drill_export'"]
        params: List[Any] = []
        if caller_id is not None:
            where.append("a.caller_id = %s")
            params.append(int(caller_id))
        if since:
            where.append("a.occurred_at >= %s")
            params.append(since)
        if until:
            where.append("a.occurred_at < %s")
            params.append(until)
        where_sql = " AND ".join(where)

        total_row = db.fetchone(
            f"SELECT COUNT(*) AS n FROM audit_log a WHERE {where_sql}",
            tuple(params),
        ) or {}
        total = int(total_row.get("n") or 0)

        rows = db.fetchall(
            f"""
            SELECT a.id, a.caller_id,
                   COALESCE(u.realname, u.username) AS caller_name,
                   a.scope_kind, a.resource_filter, a.occurred_at,
                   c.title AS chart_title
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.caller_id
            LEFT JOIN dashboard_charts c
                   ON c.id::text = a.resource_filter->>'chart_id'
            WHERE {where_sql}
            ORDER BY a.occurred_at DESC, a.id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params) + (int(limit), int(offset)),
        )
        items = [
            {
                "id": r.get("id"),
                "caller_id": r.get("caller_id"),
                "caller_name": r.get("caller_name"),
                "scope_kind": r.get("scope_kind"),
                "resource_filter": r.get("resource_filter") or {},
                "chart_title": r.get("chart_title"),
                "occurred_at": (
                    r["occurred_at"].isoformat()
                    if r.get("occurred_at") is not None else None
                ),
            }
            for r in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ----- ADMIN: refresh runtime schema ---------------------------------

    @router.post("/_admin/refresh_schema")
    def refresh_schema_route(
        request: Request, db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        """Force-rebuild views + reload custom-field dimensions.

        Called automatically from the field-builder route after a new
        column is added (so admins don't need to wait for the 60s cache).
        Manual invocation requires admin.
        """
        uid = _user_id_from_request(request, app)
        ctx = _user_context(db, uid)
        if not ctx["is_admin"]:
            raise HTTPException(403, "admin only")
        force_refresh_registry(db)
        reg = _registry(db)
        return {
            "ok": True,
            "entities": reg.entity_names(),
            "extension_summary": {
                n: {
                    "dimensions": [d.name for d in reg.entity(n).dimensions],
                    "optional_columns": list(reg.entity(n).detail_columns.optional),
                }
                for n in reg.entity_names()
            },
        }

    app.include_router(router)
