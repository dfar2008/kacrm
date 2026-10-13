"""Pure-method tests for :class:`hermes.crm.save.Save`.

Covers ``Save._parse_options`` — the instance method that normalises
``options`` metadata (comma string / JSON array / Python list) into a
clean ``List[str]``. The method does not read ``self`` state, so it can be
called via ``Save(None)._parse_options(…)`` without any DB.
"""
from __future__ import annotations


def _save():
    from hermes.crm.save import Save

    return Save(None)


# ---------------------------------------------------------------------------
# _parse_options — comma string
# ---------------------------------------------------------------------------


def test_parse_options_csv_simple():
    assert _save()._parse_options("a,b,c") == ["a", "b", "c"]


def test_parse_options_csv_with_spaces():
    assert _save()._parse_options("active, inactive , pending") == [
        "active",
        "inactive",
        "pending",
    ]


def test_parse_options_csv_trailing_comma():
    # Trailing empty after comma is dropped by the strip+filter
    result = _save()._parse_options("a,b,")
    assert result == ["a", "b"]


def test_parse_options_csv_single_value():
    assert _save()._parse_options("only_one") == ["only_one"]


# ---------------------------------------------------------------------------
# _parse_options — JSON array string
# ---------------------------------------------------------------------------


def test_parse_options_json_array_string():
    assert _save()._parse_options('["x", "y", "z"]') == ["x", "y", "z"]


def test_parse_options_json_array_integers():
    # JSON arrays of ints are coerced to str
    result = _save()._parse_options("[1, 2, 3]")
    assert result == ["1", "2", "3"]


def test_parse_options_json_array_with_empty_items():
    result = _save()._parse_options('["a", "", "b"]')
    assert result == ["a", "b"]


def test_parse_options_malformed_json_array_falls_back_to_split():
    # "[a,b" is not valid JSON but starts with "[" → split inside brackets
    result = _save()._parse_options("[a,b")
    assert "a" in result or "b" in result  # fallback split gives something


# ---------------------------------------------------------------------------
# _parse_options — Python list input
# ---------------------------------------------------------------------------


def test_parse_options_list_of_strings():
    assert _save()._parse_options(["foo", "bar"]) == ["foo", "bar"]


def test_parse_options_list_of_ints():
    assert _save()._parse_options([1, 2, 3]) == ["1", "2", "3"]


def test_parse_options_list_with_spaces():
    result = _save()._parse_options(["  a  ", "b"])
    assert result == ["a", "b"]


def test_parse_options_list_with_empty_items():
    result = _save()._parse_options(["a", "", "  ", "b"])
    assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# _parse_options — edge cases
# ---------------------------------------------------------------------------


def test_parse_options_none_returns_empty():
    assert _save()._parse_options(None) == []


def test_parse_options_empty_string_returns_empty():
    assert _save()._parse_options("") == []


def test_parse_options_whitespace_only_returns_empty():
    assert _save()._parse_options("   ") == []


def test_parse_options_non_string_non_list_returns_empty():
    assert _save()._parse_options(42) == []
    assert _save()._parse_options({"key": "val"}) == []


# ---------------------------------------------------------------------------
# _mirror_tasks_typed_fk
# ---------------------------------------------------------------------------


def _save_for_module(table_name: str):
    from hermes.crm.save import Save
    s = Save(None)
    s.table_name = table_name
    return s


def test_mirror_tasks_typed_fk_fills_accounts_id_when_related_module_is_accounts():
    s = _save_for_module("tasks")
    data = {"related_module": "accounts", "related_record_id": 426}
    s._mirror_tasks_typed_fk(data)
    assert data["accounts_id"] == 426


def test_mirror_tasks_typed_fk_fills_contacts_id_when_related_module_is_contacts():
    s = _save_for_module("tasks")
    data = {"related_module": "contacts", "related_record_id": 144}
    s._mirror_tasks_typed_fk(data)
    assert data["contacts_id"] == 144


def test_mirror_tasks_typed_fk_skips_when_typed_fk_already_set():
    """Don't override caller's explicit choice."""
    s = _save_for_module("tasks")
    data = {"related_module": "accounts", "related_record_id": 426, "accounts_id": 999}
    s._mirror_tasks_typed_fk(data)
    assert data["accounts_id"] == 999


def test_mirror_tasks_typed_fk_skips_for_unknown_related_module():
    """potentials / salesorders aren't typed columns on tasks — leave alone."""
    s = _save_for_module("tasks")
    data = {"related_module": "potentials", "related_record_id": 347}
    s._mirror_tasks_typed_fk(data)
    assert "accounts_id" not in data
    assert "contacts_id" not in data


def test_mirror_tasks_typed_fk_noop_for_non_tasks_table():
    """Don't touch data for any other module — this is tasks-only."""
    s = _save_for_module("accounts")
    data = {"related_module": "accounts", "related_record_id": 426}
    s._mirror_tasks_typed_fk(data)
    assert "accounts_id" not in data


# ---------------------------------------------------------------------------
# _guard_order_has_products
# ---------------------------------------------------------------------------

import pytest

from hermes.crm.save import FieldValidationException


@pytest.mark.parametrize("table", ["salesorders", "quotes"])
def test_guard_insert_rejects_missing_products(table):
    """INSERT with no products key (None) must reject — products mandatory."""
    s = _save_for_module(table)
    with pytest.raises(FieldValidationException):
        s._guard_order_has_products(None, is_update=False)


@pytest.mark.parametrize("table", ["salesorders", "quotes"])
def test_guard_insert_rejects_empty_products(table):
    s = _save_for_module(table)
    with pytest.raises(FieldValidationException):
        s._guard_order_has_products([], is_update=False)


@pytest.mark.parametrize("table", ["salesorders", "quotes"])
def test_guard_insert_allows_with_products(table):
    s = _save_for_module(table)
    s._guard_order_has_products([{"products_id": 1, "quantity": 1}], is_update=False)


def test_guard_insert_no_longer_accepts_final_amount_alone():
    """The old final_amount escape hatch is gone: amount without product
    lines is no longer enough to create a salesorder."""
    s = _save_for_module("salesorders")
    with pytest.raises(FieldValidationException):
        # final_amount lives in `data`, which the guard no longer inspects.
        s._guard_order_has_products(None, is_update=False)


@pytest.mark.parametrize("table", ["salesorders", "quotes"])
def test_guard_update_rejects_explicit_clear(table):
    """UPDATE that clears products to empty must reject."""
    s = _save_for_module(table)
    with pytest.raises(FieldValidationException):
        s._guard_order_has_products([], is_update=True)


@pytest.mark.parametrize("table", ["salesorders", "quotes"])
def test_guard_update_allows_untouched_products(table):
    """UPDATE that doesn't touch products (None) leaves existing rows alone."""
    s = _save_for_module(table)
    s._guard_order_has_products(None, is_update=True)


@pytest.mark.parametrize("table", ["salesorders", "quotes"])
def test_guard_update_allows_replacement(table):
    s = _save_for_module(table)
    s._guard_order_has_products([{"products_id": 2, "quantity": 3}], is_update=True)


@pytest.mark.parametrize("is_update", [True, False])
@pytest.mark.parametrize("line_items", [None, [], [{"products_id": 1}]])
def test_guard_noop_for_non_product_modules(is_update, line_items):
    """accounts / leads / contacts etc. never carry products — never guarded."""
    s = _save_for_module("accounts")
    s._guard_order_has_products(line_items, is_update=is_update)
