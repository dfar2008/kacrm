"""Pure-logic tests for field_provisioning — SQL type resolution.

No DB: SQL_TYPE_MAP and resolve_sql_type are pure functions.
"""
from __future__ import annotations

import pytest

from hermes_cloud.app.crm.field_provisioning import (
    SQL_TYPE_MAP,
    resolve_sql_type,
)


@pytest.mark.parametrize("ftype,expected", [
    ("text", "TEXT"),
    ("textarea", "TEXT"),
    ("email", "TEXT"),
    ("phone", "TEXT"),
    ("url", "TEXT"),
    ("richtext", "TEXT"),
    ("file", "TEXT"),
    ("image", "TEXT"),
    ("multiselect", "TEXT"),
    ("picklist", "VARCHAR(250)"),
    ("number", "NUMERIC"),
    ("currency", "NUMERIC"),
    ("formula", "NUMERIC"),
    ("date", "DATE"),
    ("datetime", "TIMESTAMP WITH TIME ZONE"),
    ("boolean", "BOOLEAN"),
    ("user", "INTEGER"),
    ("department", "INTEGER"),
    ("relation", "INTEGER"),
    ("autoNumber", "VARCHAR(64)"),
])
def test_resolve_sql_type_known(ftype, expected):
    assert resolve_sql_type(ftype) == expected


def test_resolve_sql_type_is_case_insensitive():
    # autoNumber stored mixed-case in metadata; lookup must normalize.
    assert resolve_sql_type("AUTONUMBER") == "VARCHAR(64)"
    assert resolve_sql_type("Number") == "NUMERIC"


def test_resolve_sql_type_unknown_returns_none():
    assert resolve_sql_type("hologram") is None
    assert resolve_sql_type("") is None


def test_sql_type_map_keys_are_lowercase():
    # The map is keyed lowercase; resolve_sql_type lowercases input.
    for key in SQL_TYPE_MAP:
        assert key == key.lower()
