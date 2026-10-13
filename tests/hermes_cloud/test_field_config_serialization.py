"""field_config must survive every field-serialization path so the
frontend's i18n labels + formula/autonumber config keep working
(spec §6.4). get_module_fields uses SELECT * (auto-carries it);
EditQuery/DetailQuery field serialization is verified here.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes.crm.edit_query import EditQuery


_MODULE = {"id": 1, "name": "accounts", "table_name": "accounts"}

_FIELD_WITH_CONFIG = {
    "field_name": "order_no",
    "field_type": "autoNumber",
    "default_value": None,
    "is_hidden": False,
    "is_readonly": False,
    "field_config": {"autonumber": {"prefix": "SO-", "digits": 5,
                                    "start": 1, "date_token": ""}},
}


def test_edit_query_form_fields_carry_field_config():
    db = MagicMock()
    with patch("hermes.crm.edit_query.ModulesDb") as MDb, \
         patch("hermes.crm.edit_query.ModuleFieldsDb") as MFDb:
        MDb.return_value.get_module_info.return_value = _MODULE
        MFDb.return_value.get_editable_fields.return_value = [
            _FIELD_WITH_CONFIG]
        eq = EditQuery(db)
        eq.set_module(1).set_record_id(0)
        out = eq.execute(current_user_id=None)
    form_fields = out["data"]["form_fields"]
    assert len(form_fields) == 1
    assert "field_config" in form_fields[0]
    assert form_fields[0]["field_config"] == \
        _FIELD_WITH_CONFIG["field_config"]
