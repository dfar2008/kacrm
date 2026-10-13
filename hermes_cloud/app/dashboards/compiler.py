"""DSL → parameterized SQL compiler (spec §4.f).

Two entry points:

- ``compile_dsl(dsl, registry, caller_id, grants)`` — normal query path.
- ``compile_drill(entity_name, scope, caller_id, dim_filters, extra_columns,
  registry, grants, limit)`` — single-chart drill-to-detail (spec §6.g):
  same view, no GROUP BY, dim values become extra filters, returns rows
  rather than aggregates.

Both:
  * enforce entity / dimension / measure presence in the registry
  * enforce caller grants for any measure or detail-column with a gate
  * inject ``row_filter[scope]`` with parameterized binding for caller_id
  * never concatenate caller-supplied strings; all values are bound

Output is a ``CompiledQuery(sql, params, columns)`` ready for
``DbWrapper.fetchall(sql, tuple_args)`` — but note we use psycopg ``%(name)s``
named placeholders so the routes layer can pass a dict (which the wrapper
delivers via ``cursor.execute(sql, params_dict)``).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, Iterable, List, Optional, Set

from .dsl import DSLQuery, DSLMeasure, DSLDimension, DSLFilter
from .semantic.model import Dimension, Entity, Measure
from .semantic.registry import Registry


class CompileError(Exception):
    """Raised when DSL is invalid against the registry / caller context."""


@dataclasses.dataclass
class CompiledQuery:
    sql: str
    params: Dict[str, Any]
    columns: List[str]


# Some measures expose underlying columns the caller may want in drill
# mode. When the column matches a gated measure's `sql`, drill inherits
# the same grant. Listed per entity for clarity.
_DRILL_COLUMN_GRANTS: Dict[str, Dict[str, str]] = {
    "customer":    {"deal_amount": "see_revenue",
                    "opportunity_amount": "see_revenue"},
    "opportunity": {"amount": "see_revenue"},
}


# --------------------------------------------------------------------------
# DSL → SQL (aggregate query)
# --------------------------------------------------------------------------


def _measure_select(measure: Measure, dsl: DSLMeasure) -> str:
    """Build a SELECT-list expression for one measure.

    Honors a per-call ``dsl.agg`` override only for measure type=="count"
    (where any aggregation makes sense via the underlying sql expression).
    For typed measures (sum / avg / median / percentile), the YAML defines
    the aggregation; ``dsl.agg`` is ignored to keep behavior predictable.
    """
    base = measure.sql or "*"
    where = f" FILTER (WHERE {measure.where})" if measure.where else ""
    alias = measure.name
    if measure.type == "count":
        # If user supplied an explicit agg=count and a column expr,
        # prefer COUNT(<col>) which is non-NULL; default to COUNT(*).
        col = base if base != "*" else "*"
        return f"COUNT({col}){where} AS {alias}"
    if measure.type == "sum":
        return f"SUM({base}){where} AS {alias}"
    if measure.type == "avg":
        return f"AVG({base}::numeric){where} AS {alias}"
    if measure.type in ("median", "percentile"):
        p = measure.percentile if measure.percentile is not None else 0.5
        # FILTER clause not valid on PERCENTILE_CONT; encode where via CASE.
        if measure.where:
            expr = f"CASE WHEN {measure.where} THEN ({base})::numeric END"
        else:
            expr = f"({base})::numeric"
        return (
            f"PERCENTILE_CONT({p}) WITHIN GROUP (ORDER BY {expr}) AS {alias}"
        )
    raise CompileError(f"unsupported measure type {measure.type!r}")


def _dim_select(dim: Dimension, d_dsl: DSLDimension) -> str:
    """Build the SELECT/GROUP-BY expression for a dimension.

    Time dims use date_trunc when granularity is set.
    """
    if dim.type == "time" and d_dsl.granularity:
        return f"date_trunc('{d_dsl.granularity}', {dim.name}) AS {dim.name}"
    return dim.name


# Date sentinels — string values that translate to SQL expressions
# rather than parameterized bindings. Lets presets express "this week"
# / "this month" / "last N days" without pre-computing dates at preset
# materialization time (which would go stale immediately).
_DATE_SENTINELS: Dict[str, str] = {
    "@now":            "now()",
    "@today":          "date_trunc('day', now())",
    "@start_of_week":  "date_trunc('week', now())",
    "@start_of_month": "date_trunc('month', now())",
    "@start_of_year":  "date_trunc('year', now())",
    "@7_days_ago":     "now() - interval '7 days'",
    "@14_days_ago":    "now() - interval '14 days'",
    "@30_days_ago":    "now() - interval '30 days'",
    "@90_days_ago":    "now() - interval '90 days'",
}


def _filter_clause(f: DSLFilter, dim: Dimension,
                   params: Dict[str, Any], idx: int) -> str:
    pname = f"flt_{idx}"
    if f.op == "is_null":
        return f"{dim.name} IS NULL"
    if f.op == "is_not_null":
        return f"{dim.name} IS NOT NULL"
    if f.op in ("eq", "neq", "gt", "gte", "lt", "lte"):
        op_sql = {"eq": "=", "neq": "<>", "gt": ">",
                  "gte": ">=", "lt": "<", "lte": "<="}[f.op]
        # Date sentinel: inline the SQL expression instead of binding
        # the literal string (which would compare a date column to text
        # and 0 rows).
        if isinstance(f.value, str) and f.value in _DATE_SENTINELS:
            return f"{dim.name} {op_sql} {_DATE_SENTINELS[f.value]}"
        params[pname] = f.value
        return f"{dim.name} {op_sql} %({pname})s"
    if f.op in ("in", "nin"):
        if not isinstance(f.value, (list, tuple)):
            raise CompileError(f"filter op {f.op!r} requires list value")
        op_sql = "IN" if f.op == "in" else "NOT IN"
        params[pname] = tuple(f.value)
        return f"{dim.name} {op_sql} %({pname})s"
    if f.op == "between":
        if not isinstance(f.value, (list, tuple)) or len(f.value) != 2:
            raise CompileError("filter op 'between' requires [lo, hi]")
        params[f"{pname}_lo"] = f.value[0]
        params[f"{pname}_hi"] = f.value[1]
        return f"{dim.name} BETWEEN %({pname}_lo)s AND %({pname}_hi)s"
    raise CompileError(f"unsupported filter op {f.op!r}")


def compile_dsl(
    q: DSLQuery,
    *,
    registry: Registry,
    caller_id: int,
    grants: Set[str],
) -> CompiledQuery:
    # 1. entity
    if not registry.has_entity(q.entity):
        raise CompileError(f"unknown entity {q.entity!r}")
    entity = registry.entity(q.entity)

    # 2. measures + grant check
    measure_objs: List[tuple[Measure, DSLMeasure]] = []
    for m_dsl in q.measures:
        if not entity.has_measure(m_dsl.name):
            raise CompileError(
                f"unknown measure {m_dsl.name!r} on entity {q.entity!r}"
            )
        m = entity.measure(m_dsl.name)
        # Self-scope sees ONLY the caller's own rows (row_filter binds
        # caller_id below), so money-shaped measures on your OWN data are
        # never gated: ``see_revenue`` (and any measure grant) guards
        # visibility into OTHERS' numbers (team / org scope), not your own.
        # An employee can always stat/analyze their own pipeline.
        if (
            m.requires_grant
            and q.scope != "self"
            and m.requires_grant not in grants
        ):
            raise CompileError(
                f"measure {m.name!r} requires grant {m.requires_grant!r}"
            )
        measure_objs.append((m, m_dsl))

    # 3. dimensions presence
    dim_objs: List[tuple[Dimension, DSLDimension]] = []
    for d_dsl in q.dimensions:
        if not entity.has_dimension(d_dsl.name):
            raise CompileError(
                f"unknown dimension {d_dsl.name!r} on entity {q.entity!r}"
            )
        dim_objs.append((entity.dimension(d_dsl.name), d_dsl))

    # 4. row filter for scope
    row_clause = entity.row_filter.for_scope(q.scope).replace(
        ":caller_id", "%(caller_id)s"
    )
    params: Dict[str, Any] = {"caller_id": caller_id}

    # 5. additional filters
    where_parts = [row_clause]
    for i, f in enumerate(q.filters):
        if not entity.has_dimension(f.dim):
            raise CompileError(
                f"filter dim {f.dim!r} unknown on entity {q.entity!r}"
            )
        where_parts.append(
            _filter_clause(f, entity.dimension(f.dim), params, i)
        )

    # 6. SELECT + columns
    select_parts: List[str] = []
    columns: List[str] = []
    group_by_parts: List[str] = []
    for dim, d_dsl in dim_objs:
        select_parts.append(_dim_select(dim, d_dsl))
        columns.append(dim.name)
        # GROUP BY uses the same expression (without alias)
        if dim.type == "time" and d_dsl.granularity:
            group_by_parts.append(f"date_trunc('{d_dsl.granularity}', {dim.name})")
        else:
            group_by_parts.append(dim.name)
    for m, m_dsl in measure_objs:
        select_parts.append(_measure_select(m, m_dsl))
        columns.append(m.name)

    # 7. ORDER BY
    order_parts: List[str] = []
    for ob in q.order_by:
        target = ob.measure or ob.dimension
        if target is None:
            raise CompileError("order_by entry must set measure or dimension")
        order_parts.append(f"{target} {ob.dir.upper()}")

    # 8. assemble
    sql = (
        f"SELECT {', '.join(select_parts)} "
        f"FROM {entity.source_view} "
        f"WHERE {' AND '.join(where_parts)}"
    )
    if group_by_parts:
        sql += " GROUP BY " + ", ".join(group_by_parts)
    if order_parts:
        sql += " ORDER BY " + ", ".join(order_parts)
    sql += f" LIMIT {min(q.limit, 10000)}"
    return CompiledQuery(sql=sql, params=params, columns=columns)


# --------------------------------------------------------------------------
# Drill mode (spec §6.g)
# --------------------------------------------------------------------------


def compile_drill(
    *,
    entity_name: str,
    scope: str,
    caller_id: int,
    dim_filters: Dict[str, Any],
    extra_columns: Iterable[str],
    registry: Registry,
    grants: Set[str],
    limit: int = 1000,
    dim_granularities: Optional[Dict[str, str]] = None,
) -> CompiledQuery:
    if not registry.has_entity(entity_name):
        raise CompileError(f"unknown entity {entity_name!r}")
    entity = registry.entity(entity_name)

    default_cols = list(entity.detail_columns.default)
    optional_cols = set(entity.detail_columns.optional)

    extras = list(extra_columns)
    for col in extras:
        if col not in optional_cols:
            raise CompileError(
                f"column {col!r} not in optional detail_columns of "
                f"{entity_name!r}"
            )
        # Same self-scope rule as compile_dsl: drilling into your OWN rows
        # never requires a measure grant; the gate only guards team/org drill.
        gate = _DRILL_COLUMN_GRANTS.get(entity_name, {}).get(col)
        if gate and scope != "self" and gate not in grants:
            raise CompileError(
                f"column {col!r} requires grant {gate!r}"
            )

    columns = default_cols + extras

    row_clause = entity.row_filter.for_scope(scope).replace(
        ":caller_id", "%(caller_id)s"
    )
    params: Dict[str, Any] = {"caller_id": caller_id}
    where_parts = [row_clause]
    grans = dim_granularities or {}
    for i, (dname, val) in enumerate(dim_filters.items()):
        if not entity.has_dimension(dname):
            raise CompileError(
                f"drill dim {dname!r} unknown on entity {entity_name!r}"
            )
        # NULL needs IS NULL — '= NULL' is always false in SQL.
        if val is None:
            where_parts.append(f"{dname} IS NULL")
            continue
        pname = f"drillf_{i}"
        params[pname] = val
        dim = entity.dimension(dname)
        gran = grans.get(dname)
        if dim.type == "time" and gran:
            # Chart used date_trunc('<gran>', col) in GROUP BY, so the
            # drill must compare on the same truncated bucket — equality
            # against the raw timestamp returns 0 rows.
            where_parts.append(
                f"date_trunc('{gran}', {dname}) "
                f"= date_trunc('{gran}', %({pname})s::timestamptz)"
            )
        else:
            where_parts.append(f"{dname} = %({pname})s")

    sql = (
        f"SELECT {', '.join(columns)} "
        f"FROM {entity.source_view} "
        f"WHERE {' AND '.join(where_parts)} "
        f"LIMIT {min(limit, 1000)}"
    )
    return CompiledQuery(sql=sql, params=params, columns=columns)
