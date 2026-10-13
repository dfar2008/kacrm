"""Pure tests for the picklist option helpers.

These cover the dedup / normalize / cap rules that the schema_changelog
HTTP routes and the copilot add_picklist_option tool both depend on.
No DB, no HTTP — just the functions in ``hermes.crm.picklist``.
"""
from __future__ import annotations

import pytest

from hermes.crm.picklist import (
    MAX_OPTION_LENGTH,
    MAX_OPTIONS_PER_FIELD,
    PicklistError,
    append_option,
    join_options,
    normalize_value,
    parse_options,
)


# ---- parse / join round-trip ----------------------------------------------

def test_parse_empty():
    assert parse_options(None) == []
    assert parse_options("") == []


def test_parse_trims_and_drops_empties():
    assert parse_options(" a , b ,, c ") == ["a", "b", "c"]


def test_join_is_canonical():
    assert join_options(["a", "b", "c"]) == "a,b,c"


def test_round_trip_preserves_order():
    raw = "制造业,SaaS,餐饮连锁"
    assert join_options(parse_options(raw)) == raw


# ---- normalize -------------------------------------------------------------

def test_normalize_trims_only():
    assert normalize_value("  SaaS  ") == "SaaS"  # case preserved


# ---- append_option: happy path --------------------------------------------

def test_append_new_value():
    new, status = append_option(["制造业"], "SaaS")
    assert status == "added"
    assert new == ["制造业", "SaaS"]


def test_append_does_not_mutate_input():
    original = ["a", "b"]
    append_option(original, "c")
    assert original == ["a", "b"]


def test_append_trims_whitespace():
    new, status = append_option([], "  hello  ")
    assert status == "added"
    assert new == ["hello"]


# ---- append_option: dedup --------------------------------------------------

def test_dedup_exact_match():
    new, status = append_option(["SaaS"], "SaaS")
    assert status == "duplicate"
    assert new == ["SaaS"]


def test_dedup_case_insensitive():
    new, status = append_option(["SaaS"], "saas")
    assert status == "duplicate"
    assert new == ["SaaS"]  # stored casing preserved


def test_dedup_after_trim():
    new, status = append_option(["SaaS"], "  SAAS  ")
    assert status == "duplicate"


# ---- append_option: validation -------------------------------------------

def test_reject_empty():
    with pytest.raises(PicklistError):
        append_option([], "")


def test_reject_whitespace_only():
    with pytest.raises(PicklistError):
        append_option([], "   ")


def test_reject_too_long():
    too_long = "x" * (MAX_OPTION_LENGTH + 1)
    with pytest.raises(PicklistError):
        append_option([], too_long)


def test_accept_exact_max_length():
    ok = "x" * MAX_OPTION_LENGTH
    new, status = append_option([], ok)
    assert status == "added"
    assert new == [ok]


def test_reject_comma_in_value():
    # Comma is the CSV delimiter — a value containing one would silently
    # split on read.
    with pytest.raises(PicklistError):
        append_option([], "a,b")


def test_hard_cap_enforced():
    saturated = [f"v{i}" for i in range(MAX_OPTIONS_PER_FIELD)]
    with pytest.raises(PicklistError):
        append_option(saturated, "one-more")


def test_hard_cap_allows_dedup_no_op():
    # Duplicate short-circuits before the cap check — we never blow up on
    # a no-op just because the field is saturated.
    saturated = [f"v{i}" for i in range(MAX_OPTIONS_PER_FIELD)]
    new, status = append_option(saturated, "v0")
    assert status == "duplicate"
    assert new == saturated


# ---- is_select_like — 0107 归一回归 ----------------------------------------
# alembic 0107 把 select/radio→picklist、multipicklist→multiselect 后,
# list/detail 的硬编码 ("select","multiSelect") 白名单匹配不到任何字段,
# 服务端选项本地化整体静默失效(2026-07 建档下拉框事故的后端半边)。
# 这里锁死:canonical + legacy 全集都必须命中。

def test_is_select_like_canonical_types():
    from hermes.crm.picklist import is_select_like
    assert is_select_like("picklist")
    assert is_select_like("multiselect")


def test_is_select_like_legacy_aliases():
    from hermes.crm.picklist import is_select_like
    for legacy in ("select", "radio", "enum", "option",
                   "multipicklist", "multiSelect", " Picklist "):
        assert is_select_like(legacy), legacy


def test_is_select_like_rejects_non_select():
    from hermes.crm.picklist import is_select_like
    for ft in (None, "", "text", "number", "user", "relation", "date"):
        assert not is_select_like(ft), ft


def test_list_query_localizes_picklist_field_options():
    """ListQuery._localize_field_options 必须处理 canonical picklist——
    不再只认 legacy 'select'。纯内存:不触库。"""
    from hermes.crm.list_query import ListQuery

    lq = ListQuery(db=object())  # 占位,当前路径不触库
    lq.table_name = "accounts"
    field = {
        "id": 1, "field_name": "industry", "field_type": "picklist",
        "options": "technology,finance",
        "field_config": {"option_labels": {
            "technology": {"zh-CN": "科技", "en-US": "Technology"},
            "finance": {"zh-CN": "金融", "en-US": "Finance"},
        }},
    }
    lq._localize_field_options([field])
    assert field["options"] != "technology,finance", (
        "picklist 字段被白名单跳过——0107 回归复发")
    assert "科技" in str(field["options"]) or "Technology" in str(field["options"])


def test_detail_query_localizes_multiselect_field_options():
    """DetailQuery 侧同款回归——canonical multiselect 也必须本地化。"""
    from hermes.crm.detail_query import DetailQuery

    dq = DetailQuery(db=object())
    dq.table_name = "accounts"
    field = {
        "id": 2, "field_name": "tags", "field_type": "multiselect",
        "options": "a,b",
        "field_config": {"option_labels": {
            "a": {"zh-CN": "甲", "en-US": "A"},
            "b": {"zh-CN": "乙", "en-US": "B"},
        }},
    }
    dq._localize_field_options([field])
    assert field["options"] != "a,b", (
        "multiselect 字段被白名单跳过——0107 回归复发")
