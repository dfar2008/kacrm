"""provision_module 归一 agent 的 initial_field 形状。"""
from hermes_cloud.app.crm.module_provisioning import _normalize_initial_field


def test_normalize_agent_shape_to_field_keys():
    out = _normalize_initial_field(
        {"name": "accounts_id", "type": "relation", "label": "客户",
         "target": "accounts", "is_required": False}
    )
    assert out["field_name"] == "accounts_id"
    assert out["field_label"] == "客户"
    assert out["field_type"] == "relation"
    assert out["field_config"] == {"relation": {"target_module": "accounts"}}
    assert out["is_required"] is False


def test_normalize_passthrough_canonical_shape():
    canon = {"field_name": "amount", "field_label": "金额", "field_type": "currency"}
    out = _normalize_initial_field(dict(canon))
    assert out["field_name"] == "amount"
    assert out["field_label"] == "金额"
    assert out["field_type"] == "currency"
    # no spurious field_config for non-relation
    assert "field_config" not in out or out["field_config"] is None


def test_normalize_non_relation_target_ignored():
    out = _normalize_initial_field({"name": "tag", "type": "text", "label": "标签", "target": "x"})
    assert out["field_type"] == "text"
    assert "field_config" not in out
