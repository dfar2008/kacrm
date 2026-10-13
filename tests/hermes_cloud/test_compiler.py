"""Compiler tests against the real semantic-layer YAMLs."""
from __future__ import annotations

import pathlib

import pytest

from hermes_cloud.app.dashboards.compiler import (
    CompileError,
    compile_drill,
    compile_dsl,
)
from hermes_cloud.app.dashboards.dsl import (
    DSLChart,
    DSLDimension,
    DSLFilter,
    DSLMeasure,
    DSLOrderBy,
    DSLQuery,
)
from hermes_cloud.app.dashboards.semantic.registry import Registry


REG = Registry.from_directory(
    pathlib.Path("hermes_cloud/app/dashboards/semantic")
)


def _q(**kwargs) -> DSLQuery:
    base = dict(
        entity="customer",
        scope="self",
        measures=[DSLMeasure(name="count")],
        dimensions=[],
        filters=[],
        chart=DSLChart(type="metric", title="x"),
    )
    base.update(kwargs)
    return DSLQuery(**base)


# ---- happy path -----------------------------------------------------------


def test_compile_simple_count():
    out = compile_dsl(_q(), registry=REG, caller_id=42, grants=set())
    assert "FROM v_dashboard_customers" in out.sql
    assert "owner_user_id = %(caller_id)s" in out.sql
    assert out.params["caller_id"] == 42
    assert "COUNT(*)" in out.sql.upper()
    assert out.columns == ["count"]


def test_compile_groupby_and_filter():
    out = compile_dsl(
        _q(
            measures=[DSLMeasure(name="count")],
            dimensions=[DSLDimension(name="industry")],
            filters=[DSLFilter(dim="status", op="eq", value="active")],
            chart=DSLChart(type="bar", title="x", x="industry", y="count"),
        ),
        registry=REG, caller_id=1, grants=set(),
    )
    assert "GROUP BY" in out.sql.upper()
    assert "industry" in out.sql
    assert "status = %(flt_0)s" in out.sql
    assert out.params["flt_0"] == "active"


def test_compile_time_granularity():
    out = compile_dsl(
        _q(
            measures=[DSLMeasure(name="count")],
            dimensions=[DSLDimension(name="created_at", granularity="week")],
            chart=DSLChart(type="line", title="x", x="created_at", y="count"),
        ),
        registry=REG, caller_id=1, grants=set(),
    )
    assert "date_trunc('week', created_at)" in out.sql


def test_compile_revenue_self_scope_never_gated():
    """Self-scope sees only your OWN rows, so revenue measures on your own
    data are never gated — an employee can always analyze their own pipeline.
    """
    q = _q(measures=[DSLMeasure(name="total_deal_amount")], scope="self")
    out = compile_dsl(q, registry=REG, caller_id=1, grants=set())
    assert "SUM(deal_amount)" in out.sql


def test_compile_revenue_team_scope_requires_grant():
    """Team/org scope reveals OTHERS' numbers — still gated by see_revenue."""
    q = _q(measures=[DSLMeasure(name="total_deal_amount")], scope="team")
    with pytest.raises(CompileError, match="grant"):
        compile_dsl(q, registry=REG, caller_id=1, grants=set())
    out = compile_dsl(q, registry=REG, caller_id=1, grants={"see_revenue"})
    assert "SUM(deal_amount)" in out.sql


def test_compile_unknown_entity():
    q = _q(entity="ghosts")
    with pytest.raises(CompileError, match="entity"):
        compile_dsl(q, registry=REG, caller_id=1, grants=set())


def test_compile_unknown_measure():
    q = _q(measures=[DSLMeasure(name="bogus")])
    with pytest.raises(CompileError, match="measure"):
        compile_dsl(q, registry=REG, caller_id=1, grants=set())


def test_compile_unknown_dimension():
    q = _q(dimensions=[DSLDimension(name="zzz")])
    with pytest.raises(CompileError, match="dimension"):
        compile_dsl(q, registry=REG, caller_id=1, grants=set())


def test_compile_filter_unknown_dim():
    q = _q(filters=[DSLFilter(dim="ghost", op="eq", value="x")])
    with pytest.raises(CompileError, match="ghost"):
        compile_dsl(q, registry=REG, caller_id=1, grants=set())


def test_compile_team_scope_uses_v_team_members():
    out = compile_dsl(
        _q(scope="team"),
        registry=REG, caller_id=42, grants=set(),
    )
    assert "v_team_members(%(caller_id)s)" in out.sql


def test_compile_org_scope_open():
    out = compile_dsl(
        _q(scope="org"),
        registry=REG, caller_id=42, grants={"see_org_overview"},
    )
    # row_filter for org is "1=1" — should appear in WHERE
    assert "WHERE 1=1" in out.sql


def test_compile_order_by_and_limit():
    out = compile_dsl(
        _q(
            measures=[DSLMeasure(name="count")],
            dimensions=[DSLDimension(name="industry")],
            order_by=[DSLOrderBy(measure="count", dir="desc")],
            limit=50,
            chart=DSLChart(type="bar", title="x", x="industry", y="count"),
        ),
        registry=REG, caller_id=1, grants=set(),
    )
    assert "ORDER BY count DESC" in out.sql
    assert "LIMIT 50" in out.sql


def test_compile_in_filter():
    out = compile_dsl(
        _q(filters=[DSLFilter(dim="industry", op="in", value=["tech", "fin"])]),
        registry=REG, caller_id=1, grants=set(),
    )
    assert "industry IN %(flt_0)s" in out.sql
    assert out.params["flt_0"] == ("tech", "fin")


def test_compile_between_filter():
    out = compile_dsl(
        _q(filters=[DSLFilter(dim="signed_at", op="between",
                              value=["2026-01-01", "2026-12-31"])]),
        registry=REG, caller_id=1, grants=set(),
    )
    assert "BETWEEN %(flt_0_lo)s AND %(flt_0_hi)s" in out.sql


def test_compile_filter_op_validation():
    q = _q(filters=[DSLFilter(dim="industry", op="in", value="not-a-list")])
    with pytest.raises(CompileError, match="list"):
        compile_dsl(q, registry=REG, caller_id=1, grants=set())


def test_compile_percentile_measure():
    out = compile_dsl(
        _q(measures=[DSLMeasure(name="sleeping_days_p50")]),
        registry=REG, caller_id=1, grants=set(),
    )
    assert "PERCENTILE_CONT(0.5)" in out.sql


# ---- drill ----------------------------------------------------------------


def test_drill_default_columns_only():
    out = compile_drill(
        entity_name="customer", scope="self", caller_id=42,
        dim_filters={"status": "active"}, extra_columns=[],
        registry=REG, grants=set(), limit=100,
    )
    # default columns from customer.yml include id/name/industry/etc.
    assert "id" in out.columns
    assert "deal_amount" not in out.columns
    assert "status = %(drillf_0)s" in out.sql
    assert "GROUP BY" not in out.sql.upper()


def test_drill_optional_column_requires_grant():
    # team scope drills into OTHERS' rows → revenue column still gated.
    with pytest.raises(CompileError, match="grant"):
        compile_drill(
            entity_name="customer", scope="team", caller_id=42,
            dim_filters={}, extra_columns=["deal_amount"],
            registry=REG, grants=set(), limit=100,
        )


def test_drill_optional_column_self_scope_never_gated():
    # self scope drills into your OWN rows → revenue column never gated.
    out = compile_drill(
        entity_name="customer", scope="self", caller_id=42,
        dim_filters={}, extra_columns=["deal_amount"],
        registry=REG, grants=set(), limit=100,
    )
    assert "deal_amount" in out.columns


def test_drill_optional_column_with_grant():
    out = compile_drill(
        entity_name="customer", scope="self", caller_id=42,
        dim_filters={}, extra_columns=["deal_amount"],
        registry=REG, grants={"see_revenue"}, limit=100,
    )
    assert "deal_amount" in out.columns


def test_drill_unknown_extra_column():
    with pytest.raises(CompileError, match="not in optional"):
        compile_drill(
            entity_name="customer", scope="self", caller_id=42,
            dim_filters={}, extra_columns=["secret_field"],
            registry=REG, grants={"see_revenue"}, limit=100,
        )


def test_drill_unknown_dim_filter():
    with pytest.raises(CompileError, match="ghost"):
        compile_drill(
            entity_name="customer", scope="self", caller_id=42,
            dim_filters={"ghost": "x"}, extra_columns=[],
            registry=REG, grants=set(), limit=100,
        )


def test_drill_team_scope():
    out = compile_drill(
        entity_name="customer", scope="team", caller_id=42,
        dim_filters={}, extra_columns=[],
        registry=REG, grants=set(), limit=100,
    )
    assert "v_team_members(%(caller_id)s)" in out.sql
