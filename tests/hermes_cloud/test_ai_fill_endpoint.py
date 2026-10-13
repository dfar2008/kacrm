from unittest.mock import patch, MagicMock
import pytest
from fastapi import HTTPException
from hermes_cloud.app.crm.routes import ai_fill_record_endpoint  # the new handler


def test_endpoint_calls_fill_record():
    db = MagicMock()
    user = {"id": 1, "username": "admin"}
    with patch("hermes_cloud.app.crm.routes._resolve_module",
               return_value={"id": 5, "name": "accounts",
                             "table_name": "accounts"}), \
         patch("hermes_cloud.app.crm.ai_attributes.fill_record") as m:
        m.return_value = {"status": "done",
                          "results": [{"field": "icp_level",
                                       "ok": True, "value": "高"}]}
        out = ai_fill_record_endpoint(
            "accounts", 7, {"fields": ["icp_level"]}, user=user, db=db)
    m.assert_called_once()
    assert out["results"][0]["value"] == "高"


def test_endpoint_rejects_non_list_fields():
    db = MagicMock()
    user = {"id": 1}
    with patch("hermes_cloud.app.crm.routes._resolve_module",
               return_value={"id": 5, "name": "accounts",
                             "table_name": "accounts"}):
        with pytest.raises(HTTPException) as ei:
            ai_fill_record_endpoint(
                "accounts", 7, {"fields": "notalist"}, user=user, db=db)
    assert ei.value.status_code == 400
