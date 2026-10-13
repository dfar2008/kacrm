from unittest.mock import patch
from hermes_cloud.app.crm import ai_attributes as ai


def test_design_builds_instruction_and_returns_config():
    captured = {}

    def fake_http(user_id, instruction, timeout_sec):
        captured["instruction"] = instruction
        return {"output_type": "picklist", "label": "客户分级", "mission": "判断重点程度",
                "context_rules": "结合行业规模", "output_spec": "只返回 高/中/低",
                "options": ["高", "中", "低"], "with_interactions": False}

    with patch.object(ai, "_dispatch_via_http", fake_http):
        cfg = ai.design_field_config(user_id=1, intent="判断客户值不值得重点跟进",
                                     module="accounts", field_names=["industry", "employee_count"])
    assert cfg["output_type"] == "picklist" and cfg["options"] == ["高", "中", "低"]
    assert "判断客户值不值得重点跟进" in captured["instruction"]   # 用户需求进了指令
    assert "industry" in captured["instruction"]                     # 现有字段进了指令


def test_design_none_reply_returns_none():
    with patch.object(ai, "_dispatch_via_http", lambda *a, **k: None):
        cfg = ai.design_field_config(user_id=1, intent="x", module="accounts", field_names=[])
    assert cfg is None
