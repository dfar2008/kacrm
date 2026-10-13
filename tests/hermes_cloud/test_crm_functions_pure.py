"""Pure-function tests for :mod:`hermes.crm.functions`.

Covers ``json_serializable``, ``_add_thousands_sep``, and ``ec_number_format``
— all three are pure Python with no DB access. The module-level import chain
hits ``psycopg`` via ``crm.db``, so we stub that before importing.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


# ---------------------------------------------------------------------------
# json_serializable
# ---------------------------------------------------------------------------


def test_json_serializable_none():
    from hermes.crm.functions import json_serializable

    assert json_serializable(None) is None


def test_json_serializable_datetime():
    from hermes.crm.functions import json_serializable

    dt = datetime(2026, 1, 15, 10, 30, 0)
    assert json_serializable(dt) == "2026-01-15 10:30:00"


def test_json_serializable_date():
    from hermes.crm.functions import json_serializable

    d = date(2026, 1, 15)
    assert json_serializable(d) == "2026-01-15"


def test_json_serializable_decimal():
    from hermes.crm.functions import json_serializable

    assert json_serializable(Decimal("3.14")) == pytest.approx(3.14)


def test_json_serializable_bytes():
    from hermes.crm.functions import json_serializable

    assert json_serializable(b"hello") == "hello"


def test_json_serializable_bytes_non_utf8_replaced():
    from hermes.crm.functions import json_serializable

    result = json_serializable(b"\xff\xfe")
    assert isinstance(result, str)


def test_json_serializable_dict_recursive():
    from hermes.crm.functions import json_serializable

    d = datetime(2026, 3, 1, 9, 0, 0)
    assert json_serializable({"dt": d, "n": Decimal("5.5")}) == {
        "dt": "2026-03-01 09:00:00",
        "n": 5.5,
    }


def test_json_serializable_list_recursive():
    from hermes.crm.functions import json_serializable

    assert json_serializable([date(2026, 1, 1), Decimal("1")]) == [
        "2026-01-01",
        1.0,
    ]


def test_json_serializable_tuple_recursive():
    from hermes.crm.functions import json_serializable

    result = json_serializable((date(2026, 6, 15), None))
    assert result == ["2026-06-15", None]


def test_json_serializable_scalar_passthrough():
    from hermes.crm.functions import json_serializable

    assert json_serializable(42) == 42
    assert json_serializable(3.14) == 3.14
    assert json_serializable("hello") == "hello"
    assert json_serializable(True) is True


def test_json_serializable_nested_dict_with_list():
    from hermes.crm.functions import json_serializable

    data = {"rows": [{"d": date(2026, 1, 1), "val": Decimal("100")}]}
    result = json_serializable(data)
    assert result == {"rows": [{"d": "2026-01-01", "val": 100.0}]}


# ---------------------------------------------------------------------------
# _add_thousands_sep
# ---------------------------------------------------------------------------


def test_add_thousands_sep_basic():
    from hermes.crm.functions import _add_thousands_sep

    assert _add_thousands_sep(1234, ",") == "1,234"


def test_add_thousands_sep_million():
    from hermes.crm.functions import _add_thousands_sep

    assert _add_thousands_sep(1000000, ",") == "1,000,000"


def test_add_thousands_sep_small():
    from hermes.crm.functions import _add_thousands_sep

    assert _add_thousands_sep(999, ",") == "999"


def test_add_thousands_sep_negative():
    from hermes.crm.functions import _add_thousands_sep

    assert _add_thousands_sep(-5000, ",") == "-5,000"


def test_add_thousands_sep_exact_1000():
    from hermes.crm.functions import _add_thousands_sep

    assert _add_thousands_sep(1000, ",") == "1,000"


def test_add_thousands_sep_custom_sep():
    from hermes.crm.functions import _add_thousands_sep

    assert _add_thousands_sep(1234567, ".") == "1.234.567"


# ---------------------------------------------------------------------------
# ec_number_format
# ---------------------------------------------------------------------------


def test_ec_number_format_integer_large():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format(1234) == "1,234"


def test_ec_number_format_million():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format(1000000) == "1,000,000"


def test_ec_number_format_small_no_sep():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format(999) == "999"


def test_ec_number_format_negative():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format(-1234) == "-1,234"


def test_ec_number_format_float_rounds():
    from hermes.crm.functions import ec_number_format

    # default_digits=0 so 1234.7 → "1,235" (rounds to nearest int)
    assert ec_number_format(1234.7) == "1,235"


def test_ec_number_format_invalid_string_returns_zero():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format("abc") == "0"


def test_ec_number_format_edit_mode_strips_separator():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format(1234, is_edit=True) == "1234"


def test_ec_number_format_string_number():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format("5000") == "5,000"


def test_ec_number_format_zero():
    from hermes.crm.functions import ec_number_format

    assert ec_number_format(0) == "0"


import pytest  # noqa: E402 — imported after stubs to avoid ordering issues
