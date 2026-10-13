from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from hermes_cloud.app.crm import ai_attributes as ai

AI_FIELD = {"id": 1, "field_name": "icp_level", "field_type": "picklist", "options": "高,中,低",
            "field_config": {"ai_fill": {"enabled": True, "output_type": "picklist",
              "mission": "m", "output_spec": "o", "inputs": {}}}}
PLAIN = {"id": 2, "field_name": "note", "field_type": "text", "field_config": None}

def _sess_for(db):
    @contextmanager
    def _s():
        yield db
    return _s

def test_fill_record_runs_only_ai_fields():
    db = MagicMock()
    db.fetchone.return_value = {"accounts_id": 7, "deleted": False, "owner_by": 3}
    with patch("hermes.crm.db.db_session", _sess_for(db)), \
         patch("hermes.crm.models.modules_db.ModulesDb") as MM, \
         patch("hermes.crm.models.module_fields_db.ModuleFieldsDb") as MF, \
         patch.object(ai, "build_fill_context", return_value=({"fields": {}}, ["field:x"])), \
         patch.object(ai, "dispatch_fill", return_value={"value": "高", "confidence": 0.9,
                                                         "abstained": False, "basis": [], "reason": None}) as md, \
         patch.object(ai, "apply_fill", return_value={"ok": True, "value": "高", "field": "icp_level"}) as ma, \
         patch.object(ai, "_record_run"):
        MM.return_value.get_module_by_name.return_value = {"id": 5, "name": "accounts"}
        MF.return_value.get_module_fields.return_value = [AI_FIELD, PLAIN]
        out = ai.fill_record(module_name="accounts", record_id=7, fields=None, trigger="manual")
    assert md.called and ma.called
    assert all(c.kwargs["field_meta"]["field_name"] == "icp_level" for c in ma.call_args_list)
    assert out["results"][0]["ok"]

def test_fill_record_dedup_event_trigger():
    db = MagicMock()
    db.fetchone.return_value = {"accounts_id": 7, "deleted": False}
    with patch("hermes.crm.db.db_session", _sess_for(db)), \
         patch("hermes.crm.models.modules_db.ModulesDb") as MM, \
         patch("hermes.crm.models.module_fields_db.ModuleFieldsDb") as MF, \
         patch.object(ai, "_recent_run", return_value=True), \
         patch.object(ai, "dispatch_fill") as md:
        MM.return_value.get_module_by_name.return_value = {"id": 5, "name": "accounts"}
        MF.return_value.get_module_fields.return_value = [AI_FIELD]
        out = ai.fill_record(module_name="accounts", record_id=7, fields=["icp_level"], trigger="on_change")
    md.assert_not_called()
    assert out["results"][0]["skipped"] == "deduped"
