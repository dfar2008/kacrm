from unittest.mock import patch
from hermes_cloud.app.crm import ai_attributes as ai


def _fm():
    return {"field_name": "icp_level", "field_type": "picklist",
            "options": "高,中,低",
            "field_config": {"ai_fill": {"output_type": "picklist", "mission": "判定",
                "context_rules": "规则",
                "inputs": {"fields": ["industry"]}}}}

def test_dispatch_builds_instruction_and_parses_reply():
    captured = {}
    def fake_http(user_id, instruction, timeout_sec):
        captured["instruction"] = instruction
        captured["user_id"] = user_id
        return {"value": "高", "confidence": 0.9, "basis": ["field:industry"],
                "abstained": False, "reason": None}
    with patch.object(ai, "_dispatch_via_http", fake_http):
        res = ai.dispatch_fill(user_id=1, field_meta=_fm(),
                               context={"fields": {"industry": "制造"}, "interactions": []})
    assert res["value"] == "高" and res["abstained"] is False
    assert "判定" in captured["instruction"]   # mission
    # output_spec 已退役:picklist 无 mode 推断为 classify,输出要求
    # 由 _output_guidance 按字段 options 自动生成。
    assert "[模式:classify]" in captured["instruction"]
    assert "高, 中, 低" in captured["instruction"]

def test_dispatch_none_reply_is_abstain():
    with patch.object(ai, "_dispatch_via_http", lambda *a, **k: None):
        res = ai.dispatch_fill(user_id=1, field_meta=_fm(),
                               context={"fields": {}, "interactions": []})
    assert res["abstained"] is True

def test_dispatch_non_dict_reply_is_abstain():
    with patch.object(ai, "_dispatch_via_http", lambda *a, **k: "not json"):
        res = ai.dispatch_fill(user_id=1, field_meta=_fm(),
                               context={"fields": {}, "interactions": []})
    assert res["abstained"] is True
