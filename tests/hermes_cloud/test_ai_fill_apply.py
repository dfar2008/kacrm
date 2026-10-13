from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock, patch
from hermes_cloud.app.crm.ai_attributes import apply_fill

MODULE = {"id": 5, "name": "accounts"}

def _fm(min_conf=0.5, options="高,中,低"):
    return {"id": 1, "field_name": "icp_level", "field_type": "picklist", "options": options,
            "field_config": {"ai_fill": {"output_type": "picklist", "mission": "m",
                "output_spec": "高/中/低", "min_confidence": min_conf, "inputs": {}}}}

def _fm_num(min_conf=0.5):
    return {"id": 2, "field_name": "num_field", "field_type": "number", "options": None,
            "field_config": {"ai_fill": {"output_type": "number", "mission": "m",
                "output_spec": "数字", "min_confidence": min_conf, "inputs": {}}}}

def _db():
    db = MagicMock()
    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn
    return db

def _patch_save():
    p = patch("hermes.crm.save.Save")
    MockSave = p.start()
    inst = MockSave.return_value
    for m in ("set_module", "set_record_id", "set_user", "set_source"):
        getattr(inst, m).return_value = inst
    return p, MockSave, inst

def test_valid_writes_via_save_and_meta():
    db = _db()
    p, MockSave, inst = _patch_save()
    try:
        res = apply_fill(db, module=MODULE, record_id=7, field_meta=_fm(),
                         result={"value": "高", "confidence": 0.9, "basis": [], "abstained": False, "reason": None},
                         actor_id=1, trigger="manual")
    finally:
        p.stop()
    assert res["ok"] and res["value"] == "高"
    inst.update.assert_called_once()
    assert any("jsonb_set" in str(c.args[0]) for c in db.execute.call_args_list)

def test_abstain_no_save():
    db = _db()
    p, MockSave, inst = _patch_save()
    with patch("hermes_cloud.app.interactions.emit"):
        try:
            res = apply_fill(db, module=MODULE, record_id=7, field_meta=_fm(),
                             result={"value": None, "abstained": True, "reason": "缺数据", "confidence": 0.0, "basis": []},
                             actor_id=1, trigger="manual")
        finally:
            p.stop()
    assert res["ok"] is False and res["abstained"]
    inst.update.assert_not_called()

def test_low_confidence_abstains():
    db = _db()
    p, MockSave, inst = _patch_save()
    with patch("hermes_cloud.app.interactions.emit"):
        try:
            res = apply_fill(db, module=MODULE, record_id=7, field_meta=_fm(),
                             result={"value": "高", "confidence": 0.2, "basis": [], "abstained": False, "reason": None},
                             actor_id=1, trigger="manual")
        finally:
            p.stop()
    assert res["ok"] is False
    inst.update.assert_not_called()

def test_invalid_picklist_abstains():
    db = _db()
    p, MockSave, inst = _patch_save()
    with patch("hermes_cloud.app.interactions.emit"):
        try:
            res = apply_fill(db, module=MODULE, record_id=7, field_meta=_fm(),
                             result={"value": "未知档", "confidence": 0.9, "basis": [], "abstained": False, "reason": None},
                             actor_id=1, trigger="manual")
        finally:
            p.stop()
    assert res["ok"] is False
    inst.update.assert_not_called()

def test_human_override_skips_on_event_trigger():
    db = _db()
    db.fetchone.return_value = {"v": "中", "ai_meta": {"icp_level": {"written_value": "高"}}}
    p, MockSave, inst = _patch_save()
    try:
        res = apply_fill(db, module=MODULE, record_id=7, field_meta=_fm(),
                         result={"value": "低", "confidence": 0.9, "basis": [], "abstained": False, "reason": None},
                         actor_id=1, trigger="on_change")
    finally:
        p.stop()
    assert res.get("skipped") == "human_override"
    inst.update.assert_not_called()

def test_numeric_decimal_vs_float_not_human_override():
    # C1: NUMERIC 列读回 Decimal('5'),written_value 是 float 5.0 —— 应判等,不算人工覆盖
    db = _db()
    db.fetchone.return_value = {"v": Decimal("5"), "ai_meta": {"num_field": {"written_value": 5.0}}}
    p, MockSave, inst = _patch_save()
    try:
        res = apply_fill(db, module=MODULE, record_id=7, field_meta=_fm_num(),
                         result={"value": 7.0, "confidence": 0.9, "basis": [], "abstained": False, "reason": None},
                         actor_id=1, trigger="on_change")
    finally:
        p.stop()
    assert res.get("skipped") != "human_override"
    assert res["ok"] is True
    inst.update.assert_called_once()

def test_numeric_genuine_human_change_skips():
    # C1: 真人工改了值(cur Decimal('9') vs written 5.0)—— 仍应跳过
    db = _db()
    db.fetchone.return_value = {"v": Decimal("9"), "ai_meta": {"num_field": {"written_value": 5.0}}}
    p, MockSave, inst = _patch_save()
    try:
        res = apply_fill(db, module=MODULE, record_id=7, field_meta=_fm_num(),
                         result={"value": 7.0, "confidence": 0.9, "basis": [], "abstained": False, "reason": None},
                         actor_id=1, trigger="on_change")
    finally:
        p.stop()
    assert res.get("skipped") == "human_override"
    inst.update.assert_not_called()
