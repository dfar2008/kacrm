"""Pure-function tests for :mod:`hermes.crm.field_writeback`.

Covers ``_safe_ident`` and the static ``FieldWriteback._aggregate_expr``
— both are pure Python with no DB access. The module imports
``hermes.crm.db``, which needs psycopg, so we stub that module before
importing.

The currency-envelope path (``_extract_currency_amount`` + the
``is_currency`` arg on ``_aggregate_expr``) was removed in the alembic
0079 NUMERIC migration follow-up: every amount column is plain NUMERIC
now, so aggregates apply to the column directly. Those tests are gone.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _safe_ident
# ---------------------------------------------------------------------------


def test_safe_ident_simple_name():
    from hermes.crm.field_writeback import _safe_ident

    assert _safe_ident("my_field") == '"my_field"'


def test_safe_ident_leading_underscore():
    from hermes.crm.field_writeback import _safe_ident

    assert _safe_ident("_private") == '"_private"'


def test_safe_ident_mixed_case():
    from hermes.crm.field_writeback import _safe_ident

    assert _safe_ident("AccountName") == '"AccountName"'


def test_safe_ident_alphanumeric():
    from hermes.crm.field_writeback import _safe_ident

    assert _safe_ident("field2023") == '"field2023"'


def test_safe_ident_leading_digit_raises():
    from hermes.crm.field_writeback import _safe_ident

    with pytest.raises(ValueError, match="unsafe identifier"):
        _safe_ident("1bad")


def test_safe_ident_empty_string_raises():
    from hermes.crm.field_writeback import _safe_ident

    with pytest.raises(ValueError, match="unsafe identifier"):
        _safe_ident("")


def test_safe_ident_dash_raises():
    from hermes.crm.field_writeback import _safe_ident

    with pytest.raises(ValueError, match="unsafe identifier"):
        _safe_ident("bad-name")


def test_safe_ident_space_raises():
    from hermes.crm.field_writeback import _safe_ident

    with pytest.raises(ValueError, match="unsafe identifier"):
        _safe_ident("bad name")


def test_safe_ident_sql_injection_raises():
    from hermes.crm.field_writeback import _safe_ident

    with pytest.raises(ValueError, match="unsafe identifier"):
        _safe_ident("field; DROP TABLE users")


def test_safe_ident_semicolon_raises():
    from hermes.crm.field_writeback import _safe_ident

    with pytest.raises(ValueError, match="unsafe identifier"):
        _safe_ident("a;b")


# ---------------------------------------------------------------------------
# FieldWriteback._aggregate_expr (static method)
#
# Signature is ``_aggregate_expr(calc, source_field_q)`` — amount columns
# are plain NUMERIC since alembic 0079, so aggregates hit the column
# directly with no currency-envelope unwrapping.
# ---------------------------------------------------------------------------


def test_aggregate_expr_sum():
    from hermes.crm.field_writeback import FieldWriteback

    result = FieldWriteback._aggregate_expr("sum", '"amount"')
    assert result == 'COALESCE(SUM("amount"), 0)'


def test_aggregate_expr_avg():
    from hermes.crm.field_writeback import FieldWriteback

    assert FieldWriteback._aggregate_expr("avg", '"score"') == 'AVG("score")'


def test_aggregate_expr_max():
    from hermes.crm.field_writeback import FieldWriteback

    assert FieldWriteback._aggregate_expr("max", '"val"') == 'MAX("val")'


def test_aggregate_expr_min():
    from hermes.crm.field_writeback import FieldWriteback

    assert FieldWriteback._aggregate_expr("min", '"val"') == 'MIN("val")'


def test_aggregate_expr_count():
    from hermes.crm.field_writeback import FieldWriteback

    assert FieldWriteback._aggregate_expr("count", '"amount"') == 'COUNT("amount")'


def test_aggregate_expr_unknown_calc_returns_none():
    from hermes.crm.field_writeback import FieldWriteback

    assert FieldWriteback._aggregate_expr("multiply", '"val"') is None


def test_aggregate_expr_original_returns_none():
    from hermes.crm.field_writeback import FieldWriteback

    # 'original' is handled by the caller, not by this method
    assert FieldWriteback._aggregate_expr("original", '"val"') is None
