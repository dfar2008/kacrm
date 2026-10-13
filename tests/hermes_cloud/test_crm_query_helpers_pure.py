"""Pure-function tests for CRM query helper modules.

Covers five pure helpers from:
  - :mod:`hermes.crm.list_query`:
      ``_parse_filter_in_value``, ``_is_safe_sql_identifier``,
      ``_dict_get_ci``, ``_is_computed_product_stock_qty_field``
  - :mod:`hermes.crm.detail_query`:
      ``_is_empty_rel``

All are free of DB access. The import chain touches ``crm.db`` and
``crm.functions``, so we stub those before importing.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# _parse_filter_in_value
# ---------------------------------------------------------------------------


def test_parse_filter_in_value_none():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value(None) == []


def test_parse_filter_in_value_empty_string():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value("") == []


def test_parse_filter_in_value_list_of_ints():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value([1, 2, 3]) == [1, 2, 3]


def test_parse_filter_in_value_list_of_strings():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value(["4", "5"]) == [4, 5]


def test_parse_filter_in_value_list_with_none_skipped():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value([1, None, 3]) == [1, 3]


def test_parse_filter_in_value_comma_separated_string():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value("1,2,3") == [1, 2, 3]


def test_parse_filter_in_value_comma_string_with_spaces():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value("1, 2, 3") == [1, 2, 3]


def test_parse_filter_in_value_json_array_string():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value("[10, 20, 30]") == [10, 20, 30]


def test_parse_filter_in_value_json_array_string_with_strings():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value('["7", "8"]') == [7, 8]


def test_parse_filter_in_value_non_digit_list_entries_skipped():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value(["abc", "5"]) == [5]


def test_parse_filter_in_value_non_digit_csv_entries_skipped():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value("abc,5,xyz") == [5]


def test_parse_filter_in_value_malformed_json_falls_back_to_csv():
    from hermes.crm.list_query import _parse_filter_in_value

    # "[1,2" is not valid JSON, falls back to comma-split of the raw string
    result = _parse_filter_in_value("[1,2")
    # "[1" and "2" split by comma; "[1" is not a digit string → skipped
    assert result == [2]


def test_parse_filter_in_value_other_type_returns_empty():
    from hermes.crm.list_query import _parse_filter_in_value

    assert _parse_filter_in_value({"key": "val"}) == []
    assert _parse_filter_in_value(42) == []


# ---------------------------------------------------------------------------
# _is_safe_sql_identifier
# ---------------------------------------------------------------------------


def test_is_safe_sql_identifier_simple():
    from hermes.crm.list_query import _is_safe_sql_identifier

    assert _is_safe_sql_identifier("accounts") is True


def test_is_safe_sql_identifier_with_underscore():
    from hermes.crm.list_query import _is_safe_sql_identifier

    assert _is_safe_sql_identifier("accounts_name") is True


def test_is_safe_sql_identifier_with_digit():
    from hermes.crm.list_query import _is_safe_sql_identifier

    assert _is_safe_sql_identifier("field2") is True


def test_is_safe_sql_identifier_empty_returns_false():
    from hermes.crm.list_query import _is_safe_sql_identifier

    assert _is_safe_sql_identifier("") is False


def test_is_safe_sql_identifier_dash_returns_false():
    from hermes.crm.list_query import _is_safe_sql_identifier

    assert _is_safe_sql_identifier("bad-name") is False


def test_is_safe_sql_identifier_space_returns_false():
    from hermes.crm.list_query import _is_safe_sql_identifier

    assert _is_safe_sql_identifier("bad name") is False


def test_is_safe_sql_identifier_semicolon_returns_false():
    from hermes.crm.list_query import _is_safe_sql_identifier

    assert _is_safe_sql_identifier("a;b") is False


# ---------------------------------------------------------------------------
# _dict_get_ci
# ---------------------------------------------------------------------------


def test_dict_get_ci_exact_key():
    from hermes.crm.list_query import _dict_get_ci

    assert _dict_get_ci({"Name": "ACME"}, "Name") == "ACME"


def test_dict_get_ci_case_insensitive():
    from hermes.crm.list_query import _dict_get_ci

    assert _dict_get_ci({"Name": "ACME"}, "name") == "ACME"


def test_dict_get_ci_none_dict():
    from hermes.crm.list_query import _dict_get_ci

    assert _dict_get_ci(None, "key") is None


def test_dict_get_ci_empty_dict():
    from hermes.crm.list_query import _dict_get_ci

    assert _dict_get_ci({}, "key") is None


def test_dict_get_ci_missing_key():
    from hermes.crm.list_query import _dict_get_ci

    assert _dict_get_ci({"other": 1}, "key") is None


def test_dict_get_ci_uppercase_key_lowercase_dict():
    from hermes.crm.list_query import _dict_get_ci

    assert _dict_get_ci({"status": "open"}, "STATUS") == "open"


# ---------------------------------------------------------------------------
# _is_computed_product_stock_qty_field
# ---------------------------------------------------------------------------


def test_is_computed_stockout_for_salesorders():
    from hermes.crm.list_query import _is_computed_product_stock_qty_field

    assert _is_computed_product_stock_qty_field("salesorders", "stockout_quantity") is True


def test_is_computed_stockin_for_purchasesorders():
    from hermes.crm.list_query import _is_computed_product_stock_qty_field

    assert _is_computed_product_stock_qty_field("purchasesorders", "stockin_quantity") is True


def test_is_computed_wrong_module_returns_false():
    from hermes.crm.list_query import _is_computed_product_stock_qty_field

    assert _is_computed_product_stock_qty_field("accounts", "stockout_quantity") is False


def test_is_computed_wrong_field_returns_false():
    from hermes.crm.list_query import _is_computed_product_stock_qty_field

    assert _is_computed_product_stock_qty_field("salesorders", "quantity") is False


def test_is_computed_empty_args_returns_false():
    from hermes.crm.list_query import _is_computed_product_stock_qty_field

    assert _is_computed_product_stock_qty_field("", "") is False


def test_is_computed_stockin_salesorders_false():
    from hermes.crm.list_query import _is_computed_product_stock_qty_field

    assert _is_computed_product_stock_qty_field("salesorders", "stockin_quantity") is False


# ---------------------------------------------------------------------------
# _is_empty_rel
# ---------------------------------------------------------------------------


def test_is_empty_rel_none():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel(None) is True


def test_is_empty_rel_zero_int():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel(0) is True


def test_is_empty_rel_zero_float():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel(0.0) is True


def test_is_empty_rel_empty_string():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel("") is True


def test_is_empty_rel_string_zero():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel("0") is True


def test_is_empty_rel_whitespace_string():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel("  ") is True


def test_is_empty_rel_nonzero_int():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel(5) is False


def test_is_empty_rel_nonempty_string():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel("hello") is False


def test_is_empty_rel_string_nonzero_number():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel("42") is False


def test_is_empty_rel_dict_not_empty():
    from hermes.crm.detail_query import _is_empty_rel

    assert _is_empty_rel({"id": 1}) is False
