from unittest.mock import MagicMock
from hermes_cloud.app.crm.workflow_provisioning import provision_workflow


def test_provision_workflow_inserts_and_returns_id():
    db = MagicMock()
    db.fetchone.return_value = {"id": 21}
    spec = {"name": "dunning", "trigger_type": "event", "module": "leads",
            "trigger_on": "create", "schedule_cron": None,
            "condition": {}, "actions": [{"x": 1}]}
    out = provision_workflow(db, spec, actor={"id": 9, "username": "wang"})
    assert out["id"] == 21
    sql = db.fetchone.call_args[0][0]
    assert "INSERT INTO workflows" in sql
    params = db.fetchone.call_args[0][1]
    assert 9 in params   # created_by = actor id
