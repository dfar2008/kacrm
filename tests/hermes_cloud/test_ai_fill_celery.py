from unittest.mock import patch
from hermes_cloud.app.celery_app import ai_fill_task, ai_fill_backfill_task


def test_ai_fill_task_delegates():
    with patch("hermes_cloud.app.crm.ai_attributes.fill_record") as m:
        m.return_value = {"status": "done", "results": []}
        out = ai_fill_task("accounts", 7, ["icp_level"], "on_change")
    m.assert_called_once()
    assert out["status"] == "done"


def test_ai_fill_task_swallows_errors():
    with patch("hermes_cloud.app.crm.ai_attributes.fill_record", side_effect=RuntimeError("boom")):
        out = ai_fill_task("accounts", 7, None, "on_change")
    assert out["status"] == "failed"


def test_backfill_task_exists():
    assert callable(ai_fill_backfill_task)
