"""Unit tests for runtime_schema — view rebuild + registry extension.

Uses a FakeDb that records executed SQL + canned discovery results so
we can verify behavior without a live PG.
"""
from __future__ import annotations

import pathlib
from typing import Any, List, Tuple

import pytest

from hermes_cloud.app.dashboards.runtime_schema import (
    extend_registry_with_custom_fields,
    rebuild_dashboard_views,
    refresh_runtime_schema,
)
from hermes_cloud.app.dashboards.semantic.registry import Registry


REG = Registry.from_directory(
    pathlib.Path("hermes_cloud/app/dashboards/semantic")
)


class FakeDb:
    def __init__(self, *,
                 columns_by_table: dict[str, List[Tuple[str, str]]] | None = None,
                 fields_by_module: dict[str, List[dict]] | None = None):
        self._cols = columns_by_table or {}
        self._fields = fields_by_module or {}
        self.executed: List[str] = []

    def fetchall(self, sql: str, args=None):
        if "information_schema.columns" in sql:
            tbl = args[0]
            return [{"column_name": c, "data_type": t} for c, t in self._cols.get(tbl, [])]
        if "module_fields" in sql:
            mod = args[0]
            return [dict(r) for r in self._fields.get(mod, [])]
        return []

    def fetchone(self, sql: str, args=None):
        return None

    def execute(self, sql: str, args=None, commit: bool = True):
        self.executed.append(sql)
        return 0

    # ``rebuild_dashboard_views`` wraps each entity's DROP+CREATE pair
    # in ``db.transaction()`` to keep them atomic (so a concurrent
    # chart query can never see a "view does not exist" gap). The real
    # DbWrapper returns a context manager; the stub is a no-op.
    from contextlib import contextmanager
    @contextmanager
    def transaction(self):
        yield


# ---- rebuild_dashboard_views ---------------------------------------------


def test_rebuild_emits_drop_and_create_per_module_entity():
    db = FakeDb(columns_by_table={
        "accounts": [("accounts_id", "bigint"), ("accounts_name", "text"),
                     ("owner_by", "bigint"), ("deleted", "boolean"),
                     ("custom_zone", "text")],
        "contacts": [("contacts_id", "bigint"), ("contacts_name", "text"),
                     ("owner_by", "bigint"), ("deleted", "boolean")],
        "leads": [("leads_id", "bigint"), ("leads_name", "text"),
                  ("owner_by", "bigint"), ("deleted", "boolean")],
        "potentials": [("potentials_id", "bigint"), ("potentials_name", "text"),
                       ("owner_by", "bigint"), ("deleted", "boolean")],
        "tasks": [("tasks_id", "bigint"), ("tasks_name", "text"),
                  ("owner_by", "bigint"), ("deleted", "boolean")],
    })
    rebuilt = rebuild_dashboard_views(db, REG)
    # 5 module-backed entities -> 5 DROPs + 5 CREATEs = 10 statements
    assert set(rebuilt) == {"customer", "contact", "lead", "opportunity", "task"}
    drops = [s for s in db.executed if s.startswith("DROP VIEW")]
    creates = [s for s in db.executed if s.startswith("CREATE VIEW")]
    assert len(drops) == 5
    assert len(creates) == 5


def test_rebuild_passes_custom_columns_through_unchanged():
    db = FakeDb(columns_by_table={
        "accounts": [
            ("accounts_id", "bigint"),
            ("accounts_name", "text"),
            ("owner_by", "bigint"),
            ("custom_lifetime_value", "numeric"),  # custom field
            ("deleted", "boolean"),
        ],
    })
    rebuild_dashboard_views(db, REG)
    create_sql = next(s for s in db.executed if "v_dashboard_customers" in s and "CREATE" in s)
    # known cols aliased
    assert '"accounts_id" AS "id"' in create_sql
    assert '"owner_by" AS "owner_user_id"' in create_sql
    # custom col passed through
    assert '"custom_lifetime_value"' in create_sql
    # deleted included as a column too (filter clause is separate WHERE)
    assert "WHERE deleted = FALSE" in create_sql


def test_rebuild_skips_entities_with_no_module_table():
    db = FakeDb()  # no columns -> rebuild is a no-op for known entities
    rebuilt = rebuild_dashboard_views(db, REG)
    # interaction/user/department/ai_action/memory have no module_table
    for non_module in ("interaction", "user", "department", "ai_action", "memory"):
        assert non_module not in rebuilt


def test_rebuild_skips_when_table_missing():
    # No columns_by_table entry -> introspect returns []
    db = FakeDb(columns_by_table={"accounts": [], "contacts": []})
    rebuilt = rebuild_dashboard_views(db, REG)
    assert rebuilt == []


# ---- extend_registry_with_custom_fields ----------------------------------


def test_extend_adds_custom_field_dimensions():
    db = FakeDb(fields_by_module={
        "accounts": [
            {"field_name": "custom_segment", "field_label": "Segment",
             "field_type": "picklist",
             "options": "smb,enterprise,strategic",
             "is_hidden": False, "is_system": False},
            {"field_name": "custom_quota", "field_label": "Quota",
             "field_type": "number",
             "options": None, "is_hidden": False, "is_system": False},
        ],
    })
    new_reg = extend_registry_with_custom_fields(db, REG)
    cust = new_reg.entity("customer")
    assert cust.has_dimension("custom_segment")
    assert cust.has_dimension("custom_quota")
    seg = cust.dimension("custom_segment")
    assert seg.type == "enum"
    assert seg.values == ("smb", "enterprise", "strategic")
    quota = cust.dimension("custom_quota")
    assert quota.type == "float"
    # Curated dims preserved
    assert cust.has_dimension("industry")


def test_extend_skips_fields_already_declared():
    # 'industry' is already a curated dimension on customer; make sure the
    # extension doesn't re-add it (would cause duplicate names → KeyError downstream).
    db = FakeDb(fields_by_module={
        "accounts": [
            {"field_name": "industry", "field_label": "Industry",
             "field_type": "text",
             "options": None, "is_hidden": False, "is_system": True},
        ],
    })
    new_reg = extend_registry_with_custom_fields(db, REG)
    cust = new_reg.entity("customer")
    industry_dims = [d for d in cust.dimensions if d.name == "industry"]
    assert len(industry_dims) == 1


def test_extend_adds_custom_fields_to_optional_drill_columns():
    db = FakeDb(fields_by_module={
        "accounts": [
            {"field_name": "custom_zone", "field_label": "Zone",
             "field_type": "text",
             "options": None, "is_hidden": False, "is_system": False},
        ],
    })
    new_reg = extend_registry_with_custom_fields(db, REG)
    cust = new_reg.entity("customer")
    assert "custom_zone" in cust.detail_columns.optional


def test_extend_no_op_for_non_module_entities():
    db = FakeDb()
    new_reg = extend_registry_with_custom_fields(db, REG)
    # interaction has no module_table → unchanged
    assert new_reg.entity("interaction") == REG.entity("interaction")


def test_refresh_runtime_schema_combines_both_steps():
    db = FakeDb(
        columns_by_table={
            "accounts": [("accounts_id", "bigint"), ("custom_x", "text"),
                         ("deleted", "boolean")],
        },
        fields_by_module={
            "accounts": [
                {"field_name": "custom_x", "field_label": "X",
                 "field_type": "text",
                 "options": None, "is_hidden": False, "is_system": False},
            ],
        },
    )
    new_reg = refresh_runtime_schema(db, REG)
    # View rebuild emitted SQL
    assert any("CREATE VIEW v_dashboard_customers" in s for s in db.executed)
    # Custom dim was added
    assert new_reg.entity("customer").has_dimension("custom_x")
