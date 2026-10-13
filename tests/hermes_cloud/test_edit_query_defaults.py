"""Tests for EditQuery default-value prefill on create (spec §6.3).

On create (record_id == 0), each field with a non-empty default_value
that isn't already populated gets its default written into data.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes.crm.edit_query import EditQuery


_MODULE = {"id": 1, "name": "accounts", "table_name": "accounts"}


def _run_create(editable_fields: list[dict]) -> dict:
    """Run EditQuery in create mode with patched gateways; return the
    'data' dict from the envelope."""
    db = MagicMock()
    with patch("hermes.crm.edit_query.ModulesDb") as MDb, \
         patch("hermes.crm.edit_query.ModuleFieldsDb") as MFDb:
        MDb.return_value.get_module_info.return_value = _MODULE
        MFDb.return_value.get_editable_fields.return_value = \
            editable_fields
        eq = EditQuery(db)
        eq.set_module(1).set_record_id(0)
        out = eq.execute(current_user_id=None)
    return out["data"]


def test_default_value_prefilled_on_create():
    data = _run_create([
        {"field_name": "stage", "field_type": "picklist",
         "default_value": "new", "is_hidden": False,
         "is_readonly": False},
    ])
    assert data["stage"] == "new"


def test_empty_default_value_not_prefilled():
    data = _run_create([
        {"field_name": "notes", "field_type": "text",
         "default_value": "", "is_hidden": False,
         "is_readonly": False},
        {"field_name": "memo", "field_type": "text",
         "default_value": None, "is_hidden": False,
         "is_readonly": False},
    ])
    assert "notes" not in data or data.get("notes") in (None, "")
    assert "memo" not in data or data.get("memo") in (None, "")


def test_default_value_does_not_override_existing():
    # owner-style prefill already set current_user; default must not
    # clobber a value already present in data.
    db = MagicMock()
    with patch("hermes.crm.edit_query.ModulesDb") as MDb, \
         patch("hermes.crm.edit_query.ModuleFieldsDb") as MFDb:
        MDb.return_value.get_module_info.return_value = _MODULE
        MFDb.return_value.get_editable_fields.return_value = [
            {"field_name": "owner", "field_type": "user",
             "default_value": "999", "is_hidden": False,
             "is_readonly": False},
        ]
        eq = EditQuery(db)
        eq.set_module(1).set_record_id(0)
        out = eq.execute(current_user_id=42)
    # owner prefilled to current user (42), NOT the default (999).
    assert out["data"]["owner"] == 42
