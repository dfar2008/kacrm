"""dashboard 自动合成自定义模块 Entity(P5)。"""
from __future__ import annotations
from unittest.mock import MagicMock


class _FakeDb:
    """按 SQL 子串路由 fetchall 的最小假 db。"""
    def fetchall(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM collections" in s and "parent_module_id" in s:
            return [{"name": "expenses", "parent_module_id": None}]
        if "information_schema.columns" in s:
            return [
                {"column_name": "expenses_id", "data_type": "bigint"},
                {"column_name": "expenses_name", "data_type": "character varying"},
                {"column_name": "owner_by", "data_type": "bigint"},
                {"column_name": "deleted", "data_type": "boolean"},
                {"column_name": "created_at", "data_type": "timestamp with time zone"},
                {"column_name": "amount", "data_type": "numeric"},
            ]
        if "FROM module_fields" in s:
            return [
                {"field_name": "amount", "field_label": "金额",
                 "field_type": "currency", "options": None,
                 "is_hidden": False, "is_system": False},
            ]
        return []


def test_synthesize_custom_module_entity():
    from hermes_cloud.app.dashboards.runtime_schema import (
        synthesize_custom_module_entities,
    )
    from hermes_cloud.app.dashboards.semantic.registry import Registry

    base = Registry([])  # 无手写 entity
    reg = synthesize_custom_module_entities(_FakeDb(), base)

    assert "expenses" in set(reg.entity_names())
    ent = reg.entity("expenses")
    assert ent.module_table == "expenses"
    assert ent.source_view == "v_dashboard_expenses"
    dims = {d.name for d in ent.dimensions}
    meas = {m.name for m in ent.measures}
    # currency 字段 → float 维度 + sum/avg measure;count 总有
    assert "amount" in dims
    assert {"count", "amount_sum", "amount_avg"} <= meas
    # owner_by 存在 → 顶层按 owner 作用域
    assert ent.row_filter.self_ == "owner_by = :caller_id"


def test_synthesize_skips_existing_and_system():
    from hermes_cloud.app.dashboards.runtime_schema import (
        synthesize_custom_module_entities, _NON_DASHBOARD_MODULES,
    )
    from hermes_cloud.app.dashboards.semantic.registry import Registry

    class _OnlySystem(_FakeDb):
        def fetchall(self, sql, params=None):
            s = " ".join(sql.split())
            if "FROM collections" in s and "parent_module_id" in s:
                return [{"name": "users", "parent_module_id": None}]
            return super().fetchall(sql, params)

    reg = synthesize_custom_module_entities(_OnlySystem(), Registry([]))
    assert "users" not in set(reg.entity_names())  # 系统表不合成
    assert "users" in _NON_DASHBOARD_MODULES
