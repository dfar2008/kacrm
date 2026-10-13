from unittest.mock import patch
import pytest
from fastapi import HTTPException
from hermes_cloud.app.crm.routes import ai_field_draft_config_endpoint


def test_endpoint_returns_config():
    db = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    user = {"id": 1}
    with patch("hermes_cloud.app.crm.routes._resolve_module", return_value={"id": 5, "name": "accounts"}), \
         patch("hermes_cloud.app.crm.routes.ModuleFieldsDb") as MF, \
         patch("hermes_cloud.app.crm.ai_attributes.design_field_config") as gen:
        MF.return_value.get_module_fields.return_value = [{"field_name": "industry"}]
        gen.return_value = {"output_type": "text", "label": "摘要", "mission": "m", "context_rules": "", "output_spec": "一句话"}
        out = ai_field_draft_config_endpoint({"intent": "一句话摘要", "module": "accounts"}, user=user, db=db)
    assert out["config"]["output_type"] == "text"


def test_endpoint_missing_intent_400():
    db = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    with pytest.raises(HTTPException) as ei:
        ai_field_draft_config_endpoint({"module": "accounts"}, user={"id": 1}, db=db)
    assert ei.value.status_code == 400
