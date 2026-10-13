"""find_pending_by_kind_key — 跨 kind 的通用去重。"""
from unittest.mock import MagicMock
from hermes.crm.models.field_proposals_db import FieldProposalsDb


def test_find_pending_by_kind_key_matches_module_add():
    db = MagicMock()
    db.fetchone.return_value = {"id": 7, "status": "pending"}
    out = FieldProposalsDb(db).find_pending_by_kind_key(
        module_id=None, kind="module_add", key="expenses"
    )
    assert out == {"id": 7, "status": "pending"}
    sql, params = db.fetchone.call_args[0]
    assert "status = 'pending'" in sql
    assert "payload->>'kind' = %s" in sql
    assert params[0] == "module_add"   # kind
    assert params[-1] == "expenses"    # key


def test_find_pending_by_kind_key_scopes_by_module_when_present():
    db = MagicMock()
    db.fetchone.return_value = None
    FieldProposalsDb(db).find_pending_by_kind_key(
        module_id=3, kind="workflow_add", key="dunning"
    )
    sql, params = db.fetchone.call_args[0]
    assert "module_id = %s" in sql
    assert 3 in params
