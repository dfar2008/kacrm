"""Tests for ModuleFieldsDb.insert_field payload assembly.

No live DB — a MagicMock DbWrapper captures the dict handed to
db.insert() so we can assert which columns get written.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from hermes.crm.models.module_fields_db import ModuleFieldsDb


def _capture_insert(data: dict) -> dict:
    """Run insert_field with a mock db; return the payload dict
    that was passed to db.insert()."""
    db = MagicMock()
    db.id.return_value = 1
    ModuleFieldsDb(db).insert_field(data)
    # db.insert(table, payload) — payload is the 2nd positional arg.
    _table, payload = db.insert.call_args[0]
    return payload


def test_insert_field_includes_field_config_when_given():
    payload = _capture_insert({
        "module_id": 1,
        "field_name": "amount",
        "field_label": "Amount",
        "field_type": "number",
        "field_config": {"formula": {"expression": "a + b"}},
    })
    assert "field_config" in payload
    assert payload["field_config"] == {"formula": {"expression": "a + b"}}


def test_insert_field_field_config_defaults_to_none():
    payload = _capture_insert({
        "module_id": 1,
        "field_name": "amount",
        "field_label": "Amount",
        "field_type": "number",
    })
    assert "field_config" in payload
    assert payload["field_config"] is None
