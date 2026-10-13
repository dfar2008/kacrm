"""Unit tests for semantic.loader + registry.

Uses tmp_path-built minimal YAML fixtures so we don't depend on the real
entity YAMLs (which Phase 2.3 will author with real schema).
"""
from __future__ import annotations

import textwrap

import pytest

from hermes_cloud.app.dashboards.semantic.loader import (
    SemanticLoadError,
    load_entity_yaml,
)
from hermes_cloud.app.dashboards.semantic.registry import Registry


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# ----- loader ---------------------------------------------------------------


def test_load_minimal_entity(tmp_path):
    p = _write(tmp_path, "x.yml", """
        entity: customer
        source_view: v_x
        row_filter: {self: "1=1", team: "1=1", org: "1=1"}
        dimensions: []
        measures: []
        detail_columns: {default: [id], optional: []}
    """)
    e = load_entity_yaml(p)
    assert e.name == "customer"
    assert e.source_view == "v_x"
    assert e.detail_columns.default == ("id",)


def test_load_full_entity(tmp_path):
    p = _write(tmp_path, "c.yml", """
        entity: customer
        source_view: accounts
        row_filter:
          self: "owner_by = :caller_id"
          team: "owner_by IN (SELECT 1)"
          org:  "1=1"
        dimensions:
          - {name: industry, type: string}
          - {name: stage, type: enum, values: [new, won, lost]}
          - {name: signed_at, type: time, granularities: [day, week]}
          - {name: owner_by, type: ref, references: user}
        measures:
          - {name: count, type: count}
          - {name: arr, type: sum, sql: deal_amount, requires_grant: see_revenue}
          - {name: median_age, type: percentile, sql: extract(day from now()), percentile: 0.5}
        detail_columns:
          default: [id, name, stage]
          optional: [deal_amount, region]
    """)
    e = load_entity_yaml(p)
    assert e.dimension("stage").values == ("new", "won", "lost")
    assert e.dimension("signed_at").granularities == ("day", "week")
    assert e.measure("arr").requires_grant == "see_revenue"
    assert e.measure("median_age").percentile == 0.5
    assert e.detail_columns.optional == ("deal_amount", "region")


def test_load_missing_entity_key(tmp_path):
    p = _write(tmp_path, "bad.yml", """
        source_view: v
        row_filter: {self: "1=1", team: "1=1", org: "1=1"}
        detail_columns: {default: [], optional: []}
    """)
    with pytest.raises(SemanticLoadError, match="entity"):
        load_entity_yaml(p)


def test_load_missing_row_filter_org(tmp_path):
    p = _write(tmp_path, "bad.yml", """
        entity: x
        source_view: v
        row_filter: {self: "1=1", team: "1=1"}
        detail_columns: {default: [], optional: []}
    """)
    with pytest.raises(SemanticLoadError, match="org"):
        load_entity_yaml(p)


def test_load_non_mapping_top(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SemanticLoadError, match="mapping"):
        load_entity_yaml(p)


# ----- registry -------------------------------------------------------------


def test_registry_loads_directory(tmp_path):
    _write(tmp_path, "a.yml", """
        entity: a
        source_view: v_a
        row_filter: {self: "1=1", team: "1=1", org: "1=1"}
        detail_columns: {default: [], optional: []}
    """)
    _write(tmp_path, "b.yml", """
        entity: b
        source_view: v_b
        row_filter: {self: "1=1", team: "1=1", org: "1=1"}
        detail_columns: {default: [], optional: []}
    """)
    reg = Registry.from_directory(tmp_path)
    assert set(reg.entity_names()) == {"a", "b"}
    assert reg.entity("a").source_view == "v_a"
    assert len(reg) == 2


def test_registry_lookup_unknown(tmp_path):
    reg = Registry([])
    with pytest.raises(KeyError):
        reg.entity("ghost")
    assert reg.has_entity("ghost") is False


def test_registry_duplicate_names_rejected(tmp_path):
    _write(tmp_path, "a1.yml", """
        entity: same
        source_view: v_1
        row_filter: {self: "1=1", team: "1=1", org: "1=1"}
        detail_columns: {default: [], optional: []}
    """)
    _write(tmp_path, "a2.yml", """
        entity: same
        source_view: v_2
        row_filter: {self: "1=1", team: "1=1", org: "1=1"}
        detail_columns: {default: [], optional: []}
    """)
    with pytest.raises(ValueError, match="duplicate"):
        Registry.from_directory(tmp_path)


def test_registry_missing_dir():
    with pytest.raises(FileNotFoundError):
        Registry.from_directory("/nonexistent/path/here")
