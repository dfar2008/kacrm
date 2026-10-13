"""Interactions query API — timeline views, record activity feeds.

Mounted at ``/api/interactions`` by :func:`main.create_app`. Provides
read-only access to the event stream. Write path is :func:`emitter.emit`.

Key query patterns:

* **Record timeline**: ``GET /api/interactions?module=accounts&record_id=17``
  uses the GIN index on ``subjects`` for fast JSON containment queries.
* **Kind filter**: ``GET /api/interactions?kind=action`` limits to CRM writes.
* **Source filter**: ``GET /api/interactions?source=agent:*`` shows agent events.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from hermes_cloud.app.auth import require_dashboard_auth
from hermes_cloud.app.config import AppConfig
from hermes.crm.db import db_session

router = APIRouter(prefix="/api/interactions", tags=["interactions"])


def _auth(request: Request) -> dict:
    config = getattr(request.app.state, "config", None) or AppConfig()
    return require_dashboard_auth(request, config)


def _caller_uid(auth: Any) -> Optional[int]:
    """Extract the calling user's int id from the dashboard-auth payload.

    ``require_dashboard_auth`` returns shapes vary by code path: dict
    with ``user_id`` / ``uid``, or a tuple ``(uid, ...)``, or just a
    raw int. We accept all three. Used to scope
    ``provenance.visibility='self_only'`` rows down to just the actor
    (spec §5.3). Returns None when the caller can't be resolved —
    visibility filter falls open (admin/system contexts).
    """
    raw: Any = None
    if isinstance(auth, dict):
        raw = auth.get("user_id") or auth.get("uid")
    elif isinstance(auth, (list, tuple)) and auth:
        raw = auth[0]
    elif isinstance(auth, (int, str)):
        raw = auth
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@router.get("")
def list_interactions(
    request: Request,
    module: Optional[str] = None,
    record_id: Optional[int] = None,
    kind: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List interactions with optional filters.

    When ``module`` + ``record_id`` are both provided, uses GIN-indexed
    ``subjects @> ...`` containment query for fast record-scoped lookups.
    """
    auth = _auth(request)
    caller_id = _caller_uid(auth)
    with db_session() as db:
        clauses: list[str] = []
        args: list = []

        # Record-scoped query via GIN index
        if module and record_id is not None:
            clauses.append("subjects @> %s::jsonb")
            import json
            args.append(json.dumps([{"module": module, "id": int(record_id)}]))
        elif module:
            # Broader module filter — slower but still useful
            clauses.append("subjects::text LIKE %s")
            args.append(f'%"module": "{module}"%')

        if kind:
            clauses.append("kind = %s")
            args.append(kind)

        if source:
            if source.endswith("*"):
                # Prefix match: source=agent:* → LIKE 'agent:%'
                clauses.append("source LIKE %s")
                args.append(source[:-1] + "%")
            else:
                clauses.append("source = %s")
                args.append(source)

        # Spec §5.3 software isolation — push the self_only visibility
        # filter into SQL so it is shared by the row query AND the
        # pagination count(*). Without this, ``total`` counts self_only
        # rows owned by other users that the Python filter below strips
        # from ``data``, producing phantom pages in the SPA paginator.
        # The regex guards the ::int cast against non-numeric actor ids.
        if caller_id is not None:
            clauses.append(
                "(COALESCE(provenance->>'visibility', '') <> 'self_only' "
                "OR ((actors->0->>'id') ~ '^-?[0-9]+$' "
                "AND (actors->0->>'id')::int = %s))"
            )
            args.append(int(caller_id))

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.extend([min(int(limit), 200), max(int(offset), 0)])

        rows = db.fetchall(
            f"""
            SELECT id, kind, occurred_at, actors, subjects, content,
                   source, draft_id, derived_from, provenance, created_at
            FROM interactions
            {where}
            ORDER BY occurred_at DESC
            LIMIT %s OFFSET %s
            """,
            tuple(args),
        )

        # Spec §5.3 software isolation: filter ``provenance.visibility=
        # self_only`` rows down to caller. Decision messages are the
        # primary self_only producer; structural isolation via
        # ``subjects=[]`` already keeps them out of record-timeline GIN
        # queries, but a caller hitting ``/api/interactions?kind=
        # copilot_message`` with no filters could otherwise see other
        # users' decision threads. Belt-and-suspenders.
        if caller_id is not None:
            from hermes_cloud.app.scope.interaction_visibility import (
                respect_interaction_visibility,
            )
            rows = respect_interaction_visibility(rows or [], caller_id=caller_id)

        # Get total count for pagination
        count_rows = db.fetchone(
            f"SELECT count(*) AS cnt FROM interactions {where}",
            tuple(args[:-2]) if args[:-2] else None,
        )
        total = int(count_rows["cnt"]) if count_rows else 0

        # Charter v2 step-4 — enrich each row's ``actors`` array with
        # human display names. Raw events store ``actors=[{type:'user',
        # id:1}]`` (or {type:'agent', name:'crm-intake'}); the SPA
        # previously rendered "1" because the backend never resolved
        # the user_id. Single batched lookup over the union of user_ids
        # in the page so we keep it O(N + 1 query).
        user_ids: set = set()
        for r in rows or []:
            for a in (r.get("actors") or []):
                if isinstance(a, dict) and a.get("type") == "user":
                    raw = a.get("id")
                    if raw is None:
                        continue
                    try:
                        user_ids.add(int(raw))
                    except (TypeError, ValueError):
                        pass
        name_by_uid: dict[int, str] = {}
        if user_ids:
            uname_rows = db.fetchall(
                "SELECT id, COALESCE(NULLIF(realname, ''), username) AS display "
                "FROM users WHERE id = ANY(%s)",
                (list(user_ids),),
            )
            for ur in (uname_rows or []):
                try:
                    name_by_uid[int(ur["id"])] = str(ur.get("display") or "")
                except (TypeError, ValueError):
                    pass
        for r in rows or []:
            new_actors = []
            for a in (r.get("actors") or []):
                if isinstance(a, dict) and a.get("type") == "user":
                    try:
                        uid = int(a.get("id"))
                    except (TypeError, ValueError):
                        new_actors.append(a)
                        continue
                    nm = name_by_uid.get(uid)
                    new_actors.append({**a, "name": a.get("name") or nm or str(uid)})
                else:
                    new_actors.append(a)
            r["actors"] = new_actors

        # Enrich every ``action`` row so the timeline reads in business
        # language: localized field labels (负责人 not owner_by) AND
        # resolved relation values (Administrator not 1, 销售部 not 2).
        _enrich_action_interactions(db, rows or [])

    return {"data": rows, "total": total, "count": len(rows)}


# Pure-plumbing columns that carry no business meaning in a timeline —
# omitted from the enriched maps so the SPA can hide them.
_TIMELINE_HIDDEN_FIELDS = frozenset({
    "memory_entity_id", "deleted", "id",
})
# Field types / names whose stored value is a FK id needing display
# resolution. Mirrors ListQuery._apply_display_values.
_USER_FIELD_TYPES = frozenset({"user", "ownerBy", "createdBy", "updatedBy"})
_USER_FIELD_NAMES = frozenset({"owner_by", "created_by", "updated_by"})
_SAFE_IDENT_RE = __import__("re").compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _enrich_action_interactions(db: Any, rows: list) -> None:
    """Attach ``content.field_labels`` and ``content.field_value_displays``
    to every ``action`` interaction.

    * ``field_labels`` — {field_name: localized business label} via
      ``localize_field_label`` (module field_label / label_i18n /
      COMMON_FIELD_LABELS fallback), locale from the request ContextVar.
    * ``field_value_displays`` — {field_name: {raw_id: display}} for
      relation-typed fields, so owner_by=1 renders "Administrator" and
      department_id=2 renders "销售部".

    Module metadata and every FK lookup are batched across the whole
    page — O(modules + relation-tables) queries, not O(rows × fields).
    """
    from hermes.crm.i18n.field_labels import localize_field_label
    from hermes.crm.i18n.options import (
        localize_picklist_options,
        parse_picklist_options,
    )
    from hermes.crm.models.module_fields_db import ModuleFieldsDb

    module_cache: dict[str, dict] = {}

    def _module_defs(mod_name: str) -> dict:
        cached = module_cache.get(mod_name)
        if cached is not None:
            return cached
        defs: dict = {"__table__": mod_name}
        mrow = db.fetchone(
            "SELECT id, table_name FROM modules WHERE name = %s", (mod_name,)
        )
        if mrow:
            defs["__table__"] = mrow.get("table_name") or mod_name
            for f in (ModuleFieldsDb(db).get_module_fields(int(mrow["id"])) or []):
                fn = str(f.get("field_name") or "")
                if fn:
                    defs[fn] = f
        module_cache[mod_name] = defs
        return defs

    # Target-table resolution for a relation field. Authoritative source is
    # field_config.relation.target_module (set by the schema builder), so a
    # second relation to the same module — which CANNOT reuse the
    # <table>_id field name — still resolves. Falls back to datainfo, then
    # the legacy <field_name>_id naming convention. Mirrors detail_query so
    # the timeline and the record form agree. Lookups are memoized.
    tbl_by_name: dict[str, Optional[str]] = {}
    tbl_by_id: dict[int, Optional[str]] = {}
    tbl_by_table: dict[str, Optional[str]] = {}

    def _module_table_by_name(name: str) -> Optional[str]:
        if name not in tbl_by_name:
            row = db.fetchone(
                "SELECT table_name FROM modules WHERE name = %s", (name,)
            )
            tbl_by_name[name] = (row or {}).get("table_name")
        return tbl_by_name[name]

    def _module_table_by_id(mid: int) -> Optional[str]:
        if mid not in tbl_by_id:
            row = db.fetchone(
                "SELECT table_name FROM modules WHERE id = %s", (mid,)
            )
            tbl_by_id[mid] = (row or {}).get("table_name")
        return tbl_by_id[mid]

    def _module_table_self(tbl: str) -> Optional[str]:
        if tbl not in tbl_by_table:
            row = db.fetchone(
                "SELECT table_name FROM modules WHERE table_name = %s", (tbl,)
            )
            tbl_by_table[tbl] = (row or {}).get("table_name")
        return tbl_by_table[tbl]

    def _relation_target_table(fdef: dict, field_name: str) -> Optional[str]:
        cfg = fdef.get("field_config")
        if isinstance(cfg, dict):
            rel = cfg.get("relation")
            target = rel.get("target_module") if isinstance(rel, dict) else None
            if target and _SAFE_IDENT_RE.match(str(target)):
                tbl = _module_table_by_name(str(target))
                if tbl:
                    return tbl
        datainfo = fdef.get("datainfo")
        if datainfo:
            parts = str(datainfo).split("#")
            if parts and parts[0].strip().isdigit():
                tbl = _module_table_by_id(int(parts[0].strip()))
                if tbl:
                    return tbl
        if field_name.endswith("_id") and _SAFE_IDENT_RE.match(field_name[:-3]):
            return _module_table_self(field_name[:-3])
        return None

    # ---- pass 1: labels + classify relation values -----------------------
    user_ids: set[int] = set()
    dept_ids: set[int] = set()
    rel_ids: dict[str, set[int]] = {}
    # (content, field_name, kind, table, [ids]) — resolved in pass 3.
    plan: list = []

    for r in rows:
        if r.get("kind") != "action":
            continue
        content = r.get("content")
        if not isinstance(content, dict):
            continue
        changes = content.get("changes")
        changes = changes if isinstance(changes, dict) else {}
        flds = content.get("fields")
        flds = flds if isinstance(flds, list) else []
        keys = ({str(k) for k in changes} | {str(x) for x in flds}) - _TIMELINE_HIDDEN_FIELDS
        if not keys:
            continue
        mod_name = ""
        for s in (r.get("subjects") or []):
            if isinstance(s, dict) and s.get("module"):
                mod_name = str(s["module"])
                break
        if not mod_name:
            continue
        defs = _module_defs(mod_name)
        table_name = str(defs.get("__table__") or mod_name)

        label_map: dict[str, str] = {}
        for k in keys:
            fdef = defs.get(k) or {}
            label_map[k] = localize_field_label(
                table_name=table_name,
                field_name=k,
                field_label=str(fdef.get("field_label") or k),
                field_id=int(fdef.get("id") or 0) or None,
                field_config=fdef.get("field_config"),
            )
        content["field_labels"] = label_map

        for k in keys:
            if k not in changes:
                continue
            fdef = defs.get(k) or {}
            ftype = str(fdef.get("field_type") or "")

            # Picklist value → localized option label (新建 not "new",
            # 飞书 not "feishu"). Resolved inline from the field's own
            # options — no DB round-trip needed.
            if ftype in ("select", "multiSelect", "picklist", "radio"):
                opts = localize_picklist_options(
                    parse_picklist_options(fdef.get("options")),
                    field_config=fdef.get("field_config"),
                )
                v2t = {
                    str(o.get("value")): str(o.get("text") or "")
                    for o in opts if o.get("value")
                }
                raw_change = changes.get(k)
                if v2t and isinstance(raw_change, dict):
                    per_field = content.setdefault(
                        "field_value_displays", {}).setdefault(k, {})
                    for vk in ("to", "from"):
                        rv = raw_change.get(vk)
                        if rv is None:
                            continue
                        for one in (rv if isinstance(rv, list) else [rv]):
                            s = str(one)
                            disp = v2t.get(s)
                            if disp and disp != s:
                                per_field[s] = disp
                continue

            kind: Optional[str] = None
            table: Optional[str] = None
            if k in _USER_FIELD_NAMES or ftype in _USER_FIELD_TYPES:
                kind = "user"
            elif k == "department_id" or ftype == "department":
                kind = "department"
            elif ftype == "relation":
                tbl = _relation_target_table(fdef, k)
                if tbl and _SAFE_IDENT_RE.match(str(tbl)):
                    kind, table = "relation", str(tbl)
            if not kind:
                continue
            raw = changes.get(k)
            ids: list[int] = []
            if isinstance(raw, dict):
                for vk in ("to", "from"):
                    if vk in raw:
                        try:
                            ids.append(int(raw[vk]))
                        except (TypeError, ValueError):
                            pass
            if not ids:
                continue
            if kind == "user":
                user_ids.update(ids)
            elif kind == "department":
                dept_ids.update(ids)
            else:
                rel_ids.setdefault(table, set()).update(ids)
            plan.append((content, k, kind, table, ids))

    if not plan:
        return

    # ---- pass 2: batch-resolve every FK id → display --------------------
    user_disp: dict[int, str] = {}
    if user_ids:
        for u in (db.fetchall(
            "SELECT id, COALESCE(NULLIF(realname, ''), username) AS d "
            "FROM users WHERE id = ANY(%s)", (list(user_ids),),
        ) or []):
            user_disp[int(u["id"])] = str(u.get("d") or "")
    dept_disp: dict[int, str] = {}
    if dept_ids:
        for d in (db.fetchall(
            "SELECT id, name AS d FROM departments WHERE id = ANY(%s)",
            (list(dept_ids),),
        ) or []):
            dept_disp[int(d["id"])] = str(d.get("d") or "")
    rel_disp: dict[str, dict[int, str]] = {}
    for table, ids in rel_ids.items():
        try:
            for row in (db.fetchall(
                f"SELECT {table}_id AS id, {table}_name AS d FROM {table} "
                f"WHERE {table}_id = ANY(%s)", (list(ids),),
            ) or []):
                rel_disp.setdefault(table, {})[int(row["id"])] = str(row.get("d") or "")
        except Exception:  # noqa: BLE001 — best-effort; raw id still renders
            pass

    # ---- pass 3: attach field_value_displays ----------------------------
    for content, k, kind, table, ids in plan:
        src = (user_disp if kind == "user"
               else dept_disp if kind == "department"
               else rel_disp.get(table or "", {}))
        fvd = content.setdefault("field_value_displays", {})
        per_field = fvd.setdefault(k, {})
        for i in ids:
            disp = src.get(i)
            if disp:
                per_field[str(i)] = disp


@router.get("/{interaction_id}")
def get_interaction(request: Request, interaction_id: str):
    """Get a single interaction by UUID."""
    auth = _auth(request)
    caller_id = _caller_uid(auth)
    with db_session() as db:
        row = db.fetchone(
            "SELECT * FROM interactions WHERE id = %s",
            (interaction_id,),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Interaction not found")
    # Self-only visibility: a caller can't fetch another user's decision
    # message even if they guess the UUID.
    if caller_id is not None:
        from hermes_cloud.app.scope.interaction_visibility import (
            respect_interaction_visibility,
        )
        filtered = respect_interaction_visibility([row], caller_id=caller_id)
        if not filtered:
            raise HTTPException(status_code=404, detail="Interaction not found")
        row = filtered[0]
    return {"data": row}
