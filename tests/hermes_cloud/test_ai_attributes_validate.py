import pytest
from hermes_cloud.app.crm.ai_attributes import validate_ai_fill, AiFillError

FIELDS = {"industry", "employee_count", "icp_level"}

def _ok():
    """合法的 picklist+classify 配置（已迁移到 4 模式体系）。"""
    return {
        "enabled": True, "mode": "classify", "output_type": "picklist",
        "mission": "判定 ICP", "context_rules": "规则", "output_spec": "高/中/低",
        "inputs": {"fields": ["industry"], "interactions": {"enabled": True, "limit": 20}},
        "tools": [], "refresh": {"on_create": True, "on_change": True, "schedule": None},
        "min_confidence": 0.0,
    }

def test_valid_passes():
    validate_ai_fill("picklist", _ok(), FIELDS)

def test_output_type_mismatch_no_longer_raises():
    """output_type 不再强制 == field_type（M1 放宽），改测 mode 不兼容仍然报错。"""
    # mode=classify 只允许 picklist/multiselect；在 text 字段上用 classify → 报错
    cfg = _ok(); cfg["output_type"] = "text"
    with pytest.raises(AiFillError):
        validate_ai_fill("text", cfg, FIELDS)   # classify on text → mode not allowed

def test_output_type_unsupported():
    cfg = _ok(); cfg["output_type"] = "richtext"
    with pytest.raises(AiFillError):
        validate_ai_fill("picklist", cfg, FIELDS)

def test_mission_required():
    cfg = _ok(); cfg["mission"] = ""
    with pytest.raises(AiFillError):
        validate_ai_fill("picklist", cfg, FIELDS)

def test_output_spec_retired_not_required():
    """output_spec 已退役——输出形状由 mode+output_type+字段选项自动生成
    (_output_guidance),空白或缺失都不再报错。"""
    cfg = _ok(); cfg["output_spec"] = "  "
    validate_ai_fill("picklist", cfg, FIELDS)
    cfg.pop("output_spec")
    validate_ai_fill("picklist", cfg, FIELDS)

def test_input_field_must_exist():
    cfg = _ok(); cfg["inputs"]["fields"] = ["nope"]
    with pytest.raises(AiFillError):
        validate_ai_fill("picklist", cfg, FIELDS)

def test_empty_input_fields_ok():
    cfg = _ok(); cfg["inputs"]["fields"] = []
    validate_ai_fill("picklist", cfg, FIELDS)
