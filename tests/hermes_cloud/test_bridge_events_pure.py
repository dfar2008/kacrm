"""Pure-function tests for :mod:`hermes_cloud.app.bridge.events`.

No DB required — these tests cover ``_values_equal`` which is pure Python.
The DB-backed tests in ``test_bridge_events.py`` are skipped whenever the
database is unreachable, making this the only always-runnable coverage for
the comparison logic that governs the "field actually changed" filter.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _values_equal
# ---------------------------------------------------------------------------


def test_values_equal_both_none():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(None, None) is True


def test_values_equal_one_none_returns_false():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(None, "") is False
    assert _values_equal("", None) is False
    assert _values_equal(None, 0) is False
    assert _values_equal(0, None) is False


def test_values_equal_int_and_float_same_value():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(1, 1.0) is True
    assert _values_equal(42, 42.0) is True


def test_values_equal_int_and_float_different_value():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(1, 2.0) is False
    assert _values_equal(1, 1.5) is False


def test_values_equal_numeric_string_coercion():
    """A string "1" should equal the int 1 (kacrm loose-compare habit)."""
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(1, "1") is True
    assert _values_equal("1.5", 1.5) is True
    assert _values_equal("42", 42) is True


def test_values_equal_string_identical():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal("hello", "hello") is True


def test_values_equal_string_different():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal("abc", "abd") is False


def test_values_equal_empty_strings():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal("", "") is True


def test_values_equal_non_numeric_string_not_equal_to_number():
    """A non-numeric string next to an int should not crash and return False."""
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal("abc", 1) is False
    assert _values_equal(1, "abc") is False


def test_values_equal_bool_and_int():
    """Python bools are ints; True==1 and False==0 via float()."""
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(True, 1) is True
    assert _values_equal(False, 0) is True
    assert _values_equal(True, 0) is False
