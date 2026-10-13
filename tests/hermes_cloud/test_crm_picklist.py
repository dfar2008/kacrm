"""Tests for :mod:`hermes.crm.picklist`.

All four functions (``parse_options``, ``join_options``, ``normalize_value``,
``append_option``) are pure Python with no DB access — no stubs required.
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


# ---------------------------------------------------------------------------
# parse_options
# ---------------------------------------------------------------------------


def test_parse_options_simple_csv():
    assert parse_options("a,b,c") == ["a", "b", "c"]


def test_parse_options_with_spaces():
    assert parse_options("  a , b , c ") == ["a", "b", "c"]


def test_parse_options_none_returns_empty():
    assert parse_options(None) == []


def test_parse_options_empty_string_returns_empty():
    assert parse_options("") == []


def test_parse_options_trailing_comma_dropped():
    assert parse_options("x,y,") == ["x", "y"]


def test_parse_options_single_value():
    assert parse_options("only") == ["only"]


def test_parse_options_preserves_order():
    result = parse_options("z,a,m")
    assert result == ["z", "a", "m"]


def test_parse_options_non_string_coerced():
    assert parse_options(42) == ["42"]


# ---------------------------------------------------------------------------
# join_options
# ---------------------------------------------------------------------------


def test_join_options_basic():
    assert join_options(["a", "b", "c"]) == "a,b,c"


def test_join_options_single():
    assert join_options(["only"]) == "only"


def test_join_options_empty_list():
    assert join_options([]) == ""


def test_join_options_roundtrip():
    original = "foo,bar,baz"
    assert join_options(parse_options(original)) == original


# ---------------------------------------------------------------------------
# normalize_value
# ---------------------------------------------------------------------------


def test_normalize_value_strips_whitespace():
    assert normalize_value("  hello  ") == "hello"


def test_normalize_value_preserves_case():
    assert normalize_value("SaaS") == "SaaS"


def test_normalize_value_empty():
    assert normalize_value("") == ""


def test_normalize_value_numeric():
    assert normalize_value(42) == "42"


# ---------------------------------------------------------------------------
# append_option — happy path
# ---------------------------------------------------------------------------


def test_append_option_adds_new_value():
    result, status = append_option(["a", "b"], "c")
    assert result == ["a", "b", "c"]
    assert status == "added"


def test_append_option_does_not_mutate_existing():
    existing = ["a", "b"]
    append_option(existing, "c")
    assert existing == ["a", "b"]


def test_append_option_trims_value():
    result, status = append_option([], "  hello  ")
    assert result == ["hello"]
    assert status == "added"


def test_append_option_case_insensitive_dedup():
    result, status = append_option(["Active"], "active")
    assert status == "duplicate"
    assert result == ["Active"]  # original casing preserved


def test_append_option_dedup_exact_match():
    result, status = append_option(["Open", "Closed"], "Open")
    assert status == "duplicate"
    assert len(result) == 2


def test_append_option_dedup_upper_variant():
    result, status = append_option(["won"], "WON")
    assert status == "duplicate"


# ---------------------------------------------------------------------------
# append_option — validation errors
# ---------------------------------------------------------------------------


def test_append_option_empty_value_raises():
    with pytest.raises(PicklistError, match="empty"):
        append_option([], "")


def test_append_option_whitespace_only_raises():
    with pytest.raises(PicklistError, match="empty"):
        append_option([], "   ")


def test_append_option_too_long_raises():
    long_val = "x" * (MAX_OPTION_LENGTH + 1)
    with pytest.raises(PicklistError, match=str(MAX_OPTION_LENGTH)):
        append_option([], long_val)


def test_append_option_exact_max_length_ok():
    ok_val = "x" * MAX_OPTION_LENGTH
    result, status = append_option([], ok_val)
    assert status == "added"


def test_append_option_comma_in_value_raises():
    with pytest.raises(PicklistError, match="','"):
        append_option([], "a,b")


def test_append_option_cap_exceeded_raises():
    full = [str(i) for i in range(MAX_OPTIONS_PER_FIELD)]
    with pytest.raises(PicklistError, match=str(MAX_OPTIONS_PER_FIELD)):
        append_option(full, "one_more")


def test_append_option_at_cap_minus_one_ok():
    almost_full = [str(i) for i in range(MAX_OPTIONS_PER_FIELD - 1)]
    result, status = append_option(almost_full, "last")
    assert status == "added"
    assert len(result) == MAX_OPTIONS_PER_FIELD
