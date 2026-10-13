"""Runtime schema discovery — make dashboards aware of custom CRM fields.

The CRM modules (accounts/contacts/leads/potentials/tasks) grow new
columns at runtime via the schema-evo field builder
(hermes_cloud/app/crm/routes/__init__.py field-creation flow):
``module_fields`` row is inserted AND ``ALTER TABLE <module> ADD COLUMN``
runs. Without this module, neither the views nor the semantic registry
would see those new fields.

Two functions:

  rebuild_dashboard_views(db)
    For each Entity with ``module_table``, drop+recreate the
    ``v_dashboard_<entity>`` view including ALL columns from the live
    table — preserving the curated normalization aliases (id, name,
    owner_user_id, signed_at) AND passing every other column through
    unchanged so custom fields are queryable.

  extend_registry_with_custom_fields(db, registry)
    Returns a NEW Registry whose Entity objects have additional
    Dimensions (one per custom field) and detail_columns.optional
    entries (so drill modal can show them).

Both are idempotent and cheap; safe to call on every refresh.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from .semantic.model import (
    DetailColumns,
    Dimension,
    Entity,
    Measure,
    RowFilter,
)
from .semantic.registry import Registry


logger = logging.getLogger(__name__)


# ---- column normalization map ----------------------------------------------
# Per entity name → {real_table_column: aliased_view_column}.
# These are the curated columns the YAML/DSL references by the right-hand
# name. ALL other columns from the table pass through unchanged.

_NORMALIZATION: Dict[str, Dict[str, str]] = {
    "customer": {
        "accounts_id": "id",
        "accounts_name": "name",
        "owner_by": "owner_user_id",
        "latest_deal_date": "signed_at",
    },
    "contact": {
        "contacts_id": "id",
        "contacts_name": "name",
        "owner_by": "owner_user_id",
    },
    "lead": {
        "leads_id": "id",
        "leads_name": "name",
        "owner_by": "owner_user_id",
    },
    "opportunity": {
        "potentials_id": "id",
        "potentials_name": "name",
        "owner_by": "owner_user_id",
        # 旧 DSL / chart 用 "status" 当作"商机阶段"，但 potentials
        # 表的真实列是 milestone（needs_analysis / negotiation /
        # proposal_sent / initial_contact / closed_won / closed_lost）。
        # 别名映射让历史 chart DSL（"dim": "status"）继续工作而不必
        # 全部数据迁移。
        "milestone": "status",
    },
    "task": {
        "tasks_id": "id",
        "tasks_name": "name",
        "owner_by": "owner_user_id",
    },
    "product": {
        "products_id": "id",
        "products_name": "name",
        "owner_by": "owner_user_id",
    },
    "order": {
        "salesorders_id": "id",
        "salesorders_name": "name",
        "owner_by": "owner_user_id",
    },
    "quote": {
        "quotes_id": "id",
        "quotes_name": "name",
        "owner_by": "owner_user_id",
    },
    "receivable": {
        "receivables_id": "id",
        "receivables_name": "name",
        "owner_by": "owner_user_id",
    },
    "payment": {
        "payments_id": "id",
        "payments_name": "name",
        "owner_by": "owner_user_id",
    },
}


# ---- pg_type -> dashboard dim type --------------------------------------

def _pg_to_dim_type(pg_type: str) -> str:
    pt = (pg_type or "").lower()
    if any(s in pt for s in ("int", "numeric", "double", "real", "bigserial")):
        if "int" in pt and "interval" not in pt:
            return "int"
        return "float"
    if any(s in pt for s in ("timestamp", "date", "time")):
        return "time"
    if "bool" in pt:
        return "bool"
    return "string"


def _field_type_to_dim_type(field_type: str) -> str:
    ft = (field_type or "").lower()
    if ft in ("number",):
        return "float"
    if ft in ("date", "datetime"):
        return "time"
    if ft in ("boolean",):
        return "bool"
    if ft in ("picklist",):
        return "enum"
    return "string"


# ---- discovery primitives ------------------------------------------------


def _discover_table_columns(db, table_name: str) -> List[Tuple[str, str]]:
    rows = db.fetchall(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    return [(r["column_name"], r["data_type"]) for r in rows]


def _discover_module_fields(db, module_name: str) -> List[Dict[str, Any]]:
    """Return module_fields rows for the given module by collection name.

    ``collections.name`` matches the table name by convention (kacrm).
    """
    rows = db.fetchall(
        """
        SELECT mf.field_name, mf.field_label, mf.field_type, mf.options,
               mf.is_hidden, mf.is_system
        FROM module_fields mf
        JOIN collections c ON c.id = mf.module_id
        WHERE c.name = %s AND mf.is_hidden = FALSE
        ORDER BY mf.sort_order, mf.id
        """,
        (module_name,),
    )
    return [dict(r) for r in rows]


# ---- public API ----------------------------------------------------------


def rebuild_dashboard_views(db, registry: Registry) -> List[str]:
    """Drop & recreate v_dashboard_<entity> for every Entity with module_table.

    Includes every column from the underlying table — known ones aliased
    per ``_NORMALIZATION``, custom ones passed through. Always filters
    ``deleted = false`` (every CRM table has this column).

    Returns list of entity names whose views were rebuilt.
    """
    rebuilt: List[str] = []
    for name in registry.entity_names():
        ent = registry.entity(name)
        if not ent.module_table:
            continue
        try:
            cols = _discover_table_columns(db, ent.module_table)
        except Exception as exc:
            logger.warning("dashboards: cannot introspect %s: %s",
                           ent.module_table, exc)
            continue
        if not cols:
            continue
        norm = _NORMALIZATION.get(name, {})
        select_parts: List[str] = []
        seen_aliases: set[str] = set()
        for col, _pg in cols:
            alias = norm.get(col, col)
            # avoid duplicate output names if a real column collides
            # with a reserved alias (very unlikely, but defensive).
            if alias in seen_aliases:
                continue
            seen_aliases.add(alias)
            if alias == col:
                select_parts.append(f'"{col}"')
            else:
                # Emit BOTH names so DSL written against the raw column
                # AND DSL written against the alias both resolve. Without
                # this an alias mapping breaks any chart that referenced
                # the underlying column by its real name. Concrete case:
                # opportunity.milestone aliased to "status" — the funnel
                # chart authored against "milestone" 500'd because the
                # view only exposed "status". Cheap to emit twice; the
                # only cost is two extra column references in the view
                # definition.
                if col not in seen_aliases:
                    seen_aliases.add(col)
                    select_parts.append(f'"{col}"')
                select_parts.append(f'"{col}" AS "{alias}"')
        select_clause = ", ".join(select_parts)
        view_name = ent.source_view
        # CREATE OR REPLACE VIEW disallows changing column count/types;
        # we must DROP first to handle custom-field additions cleanly.
        # CASCADE in case anything depends on it (nothing should — these
        # are dashboard-internal views).
        # CREATE OR REPLACE only works when column structure is unchanged;
        # since we want to support custom-field additions, we DROP first.
        # The DROP + CREATE pair MUST share one transaction — otherwise
        # a concurrent chart request can hit the gap and see
        # "relation v_dashboard_<x> does not exist". db.transaction()
        # wraps both statements in a single PG transaction.
        try:
            with db.transaction():
                db.execute(f"DROP VIEW IF EXISTS {view_name} CASCADE",
                           commit=False)
                db.execute(
                    f"CREATE VIEW {view_name} AS "
                    f"SELECT {select_clause} FROM {ent.module_table} "
                    f"WHERE deleted = FALSE",
                    commit=False,
                )
            rebuilt.append(name)
        except Exception as exc:
            logger.warning("dashboards: view rebuild failed for %s: %s",
                           name, exc)
    return rebuilt


# Reserved aliased names that the curated YAML already declares —
# we MUST NOT re-declare these as custom dimensions.
def _reserved_dim_names(ent: Entity) -> set[str]:
    return {d.name for d in ent.dimensions} | {m.name for m in ent.measures}


def extend_registry_with_custom_fields(db, registry: Registry) -> Registry:
    """Return a NEW Registry where module-backed Entities are extended
    with custom-field dimensions + detail_columns.

    Pure-functional w.r.t. the input Registry (which is treated as
    immutable curated baseline).
    """
    new_entities: List[Entity] = []
    for name in registry.entity_names():
        ent = registry.entity(name)
        if not ent.module_table:
            new_entities.append(ent)
            continue
        try:
            fields = _discover_module_fields(db, ent.module_table)
        except Exception as exc:
            logger.warning(
                "dashboards: module_fields discovery failed for %s: %s",
                ent.module_table, exc,
            )
            new_entities.append(ent)
            continue

        reserved = _reserved_dim_names(ent)
        existing_optional = set(ent.detail_columns.optional)

        added_dims: List[Dimension] = []
        added_optional: List[str] = []
        for f in fields:
            fname = f["field_name"]
            if not fname or fname in reserved:
                continue
            dim_type = _field_type_to_dim_type(f.get("field_type") or "")
            values = None
            if dim_type == "enum" and f.get("options"):
                # options is a delimited string per kacrm convention
                opts = [
                    o.strip() for o in str(f["options"]).split(",")
                    if o.strip()
                ]
                values = tuple(opts) if opts else None
            added_dims.append(Dimension(
                name=fname, type=dim_type, values=values,
            ))
            if fname not in existing_optional:
                added_optional.append(fname)

        if not added_dims and not added_optional:
            new_entities.append(ent)
            continue

        new_entities.append(Entity(
            name=ent.name,
            source_view=ent.source_view,
            row_filter=ent.row_filter,
            dimensions=ent.dimensions + tuple(added_dims),
            measures=ent.measures,
            detail_columns=DetailColumns(
                default=ent.detail_columns.default,
                optional=ent.detail_columns.optional + tuple(added_optional),
            ),
            module_table=ent.module_table,
        ))

    return Registry(new_entities)


# system/internal collections that never get a dashboard entity
_NON_DASHBOARD_MODULES = {"users", "roles", "departments", "permissions"}


def synthesize_custom_module_entities(db, registry: "Registry") -> "Registry":
    """为没有手写 yml entity 的模块(自定义模块 + 子明细)自动合成 dashboard Entity。

    手写 yml 保持权威,只补缺。视图列名原样透传(见 rebuild_dashboard_views:
    自定义模块不在 _NORMALIZATION,列名不归一化),故 Entity 引用原始列名。
    维度从 module_fields 按类型推导;数值字段额外出 sum/avg measure;count 总有。
    顶层模块按 owner_by 作用域;子明细无 owner → 全可见。
    """
    # 「已被 curated entity 覆盖」的集合名集合。curated yml 的 entity 名常≠
    # 集合名(accounts→customer / payments→payment / salesorders→order),且
    # 部分无 module_table 只有 source_view(v_dashboard_<table>)。三路都认,
    # 否则会给已覆盖的集合重复合成 entity 并把共享视图用原始列重建(回归)。
    covered_tables = set()
    existing_names = set(registry.entity_names())
    curated_views: list[str] = []
    for nm in existing_names:
        e = registry.entity(nm)
        if e.module_table:
            covered_tables.add(e.module_table)
        sv = e.source_view or ""
        if sv.startswith("v_dashboard_"):
            covered_tables.add(sv[len("v_dashboard_"):])
            curated_views.append(sv)
    # 认 curated 视图实际读的底表(order 的 v_dashboard_orders 读 salesorders 表,
    # 名字对不上)。只查 base_registry(curated)的 source_view,不碰已合成的
    # 自定义视图 —— 否则会把自定义模块自己建的 v_dashboard_<custom> 误算覆盖,
    # 反把它排除掉(鸡生蛋)。
    if curated_views:
        try:
            ph = ", ".join(["%s"] * len(curated_views))
            for r in (db.fetchall(
                "SELECT DISTINCT table_name FROM information_schema.view_table_usage "
                f"WHERE view_schema='public' AND view_name IN ({ph})",
                tuple(curated_views)) or []):
                t = r.get("table_name")
                if t:
                    covered_tables.add(t)
        except Exception:
            pass
    try:
        rows = db.fetchall(
            "SELECT name, parent_module_id FROM collections ORDER BY id")
    except Exception as exc:
        logger.warning("dashboards: collections scan failed: %s", exc)
        return registry

    synthesized: List["Entity"] = []
    for row in rows or []:
        name = row.get("name")
        if not name or name in covered_tables or name in existing_names:
            continue
        if name in _NON_DASHBOARD_MODULES:
            continue
        try:
            cols = {c for c, _ in _discover_table_columns(db, name)}
        except Exception:
            continue
        if not cols:
            continue
        is_subdetail = row.get("parent_module_id") is not None
        try:
            fields = _discover_module_fields(db, name)
        except Exception:
            fields = []

        dims: List[Dimension] = []
        measures: List[Measure] = [Measure(name="count", type="count")]
        detail_default: List[str] = []
        detail_optional: List[str] = []
        seen: set = set()

        pk = f"{name}_id"
        if pk in cols:
            detail_default.append(pk); seen.add(pk)
        head = f"{name}_name"
        if head in cols:
            detail_default.append(head); seen.add(head)

        for f in fields:
            fn = f.get("field_name")
            if not fn or fn in seen or fn not in cols:
                continue
            seen.add(fn)
            ftype = (f.get("field_type") or "").strip().lower()
            # _field_type_to_dim_type 只认 "number"(currency 会落到 string);
            # 这里把所有数值型字段统一成 float 维度并出 sum/avg measure。
            _NUMERIC = {"number", "currency", "float", "int",
                        "percent", "formula"}
            dt = "float" if ftype in _NUMERIC else _field_type_to_dim_type(ftype)
            values = None
            if dt == "enum" and f.get("options"):
                opts = [o.strip() for o in str(f["options"]).split(",") if o.strip()]
                values = tuple(opts) or None
            gran = ("day", "week", "month", "quarter", "year") if dt == "time" else None
            refs = "user" if ftype == "user" else ("department" if ftype == "department" else None)
            dims.append(Dimension(name=fn, type=dt, values=values,
                                  granularities=gran, references=refs))
            if dt in ("int", "float"):
                measures.append(Measure(name=f"{fn}_sum", type="sum", sql=fn))
                measures.append(Measure(name=f"{fn}_avg", type="avg", sql=fn))
            detail_optional.append(fn)

        if "created_at" in cols and not any(d.name == "created_at" for d in dims):
            dims.append(Dimension(name="created_at", type="time",
                                  granularities=("day", "week", "month", "quarter", "year")))
            detail_optional.append("created_at")

        if not is_subdetail and "owner_by" in cols:
            rf = RowFilter(self_="owner_by = :caller_id", team="1=1", org="1=1")
        else:
            rf = RowFilter(self_="1=1", team="1=1", org="1=1")

        synthesized.append(Entity(
            name=name,
            source_view=f"v_dashboard_{name}",
            row_filter=rf,
            dimensions=tuple(dims),
            measures=tuple(measures),
            detail_columns=DetailColumns(default=tuple(detail_default),
                                         optional=tuple(detail_optional)),
            module_table=name,
        ))

    if not synthesized:
        return registry
    base = [registry.entity(n) for n in existing_names]
    return Registry(base + synthesized)


def refresh_runtime_schema(db, base_registry: Registry) -> Registry:
    """One-shot convenience: rebuild views + extend registry.

    Call on app startup and whenever a custom field is added.
    """
    with_custom = synthesize_custom_module_entities(db, base_registry)
    rebuilt = rebuild_dashboard_views(db, with_custom)
    if rebuilt:
        logger.info("dashboards: rebuilt views for %d entities: %s",
                    len(rebuilt), ", ".join(rebuilt))
    return extend_registry_with_custom_fields(db, with_custom)
