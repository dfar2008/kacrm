"""TDD Task 1: ai_fill mode 维度校验 + mode↔field_type 匹配。"""
import pytest
from hermes_cloud.app.crm.ai_attributes import validate_ai_fill, AiFillError


def _base(field_type="text", mode=None, **extra):
    """构造最简合法 ai_fill。mode=None 表示不传（旧配置）。"""
    cfg = {"mission": "测试使命", "output_spec": "输出规格"}
    if mode is not None:
        cfg["mode"] = mode
    cfg.update(extra)
    return field_type, cfg


# ── classify ────────────────────────────────────────────────────────────────

def test_classify_on_picklist_passes():
    field_type, cfg = _base("picklist", mode="classify")
    validate_ai_fill(field_type, cfg, [])


def test_classify_on_multiselect_passes():
    field_type, cfg = _base("multiselect", mode="classify")
    validate_ai_fill(field_type, cfg, [])


def test_classify_on_text_raises():
    field_type, cfg = _base("text", mode="classify")
    with pytest.raises(AiFillError, match="not allowed"):
        validate_ai_fill(field_type, cfg, [])


def test_classify_on_number_raises():
    field_type, cfg = _base("number", mode="classify")
    with pytest.raises(AiFillError, match="not allowed"):
        validate_ai_fill(field_type, cfg, [])


# ── summarize ───────────────────────────────────────────────────────────────

def test_summarize_on_text_passes():
    field_type, cfg = _base("text", mode="summarize")
    validate_ai_fill(field_type, cfg, [])


def test_summarize_on_textarea_passes():
    field_type, cfg = _base("textarea", mode="summarize")
    validate_ai_fill(field_type, cfg, [])


def test_summarize_on_currency_raises():
    field_type, cfg = _base("currency", mode="summarize")
    with pytest.raises(AiFillError, match="not allowed"):
        validate_ai_fill(field_type, cfg, [])


# ── web_agent ────────────────────────────────────────────────────────────────

def test_web_agent_on_currency_passes():
    field_type, cfg = _base("currency", mode="web_agent")
    validate_ai_fill(field_type, cfg, [])


def test_web_agent_on_text_passes():
    field_type, cfg = _base("text", mode="web_agent")
    validate_ai_fill(field_type, cfg, [])


def test_web_agent_on_number_passes():
    field_type, cfg = _base("number", mode="web_agent")
    validate_ai_fill(field_type, cfg, [])


def test_web_agent_on_picklist_raises():
    field_type, cfg = _base("picklist", mode="web_agent")
    with pytest.raises(AiFillError, match="not allowed"):
        validate_ai_fill(field_type, cfg, [])


# ── prompt ───────────────────────────────────────────────────────────────────

def test_prompt_on_text_passes():
    field_type, cfg = _base("text", mode="prompt")
    validate_ai_fill(field_type, cfg, [])


def test_prompt_on_currency_passes():
    field_type, cfg = _base("currency", mode="prompt")
    validate_ai_fill(field_type, cfg, [])


def test_prompt_on_picklist_raises():
    field_type, cfg = _base("picklist", mode="prompt")
    with pytest.raises(AiFillError, match="not allowed"):
        validate_ai_fill(field_type, cfg, [])


# ── 未知 mode ────────────────────────────────────────────────────────────────

def test_unknown_mode_raises():
    field_type, cfg = _base("text", mode="magic")
    with pytest.raises(AiFillError, match="mode"):
        validate_ai_fill(field_type, cfg, [])


def test_unknown_mode_raises_2():
    field_type, cfg = _base("picklist", mode="auto")
    with pytest.raises(AiFillError, match="mode"):
        validate_ai_fill(field_type, cfg, [])


# ── 向后兼容：旧配置无 mode 字段 ─────────────────────────────────────────────

def test_no_mode_defaults_to_prompt_on_text():
    """旧配置没有 mode 字段 → 默认 prompt → text 字段合法，不报错。"""
    field_type, cfg = _base("text", mode=None)
    assert "mode" not in cfg
    validate_ai_fill(field_type, cfg, [])


def test_no_mode_defaults_to_prompt_on_currency():
    """旧配置没有 mode → 默认 prompt → currency 也合法。"""
    field_type, cfg = _base("currency", mode=None)
    assert "mode" not in cfg
    validate_ai_fill(field_type, cfg, [])


def test_no_mode_on_picklist_raises_because_prompt_not_allowed():
    """旧配置没有 mode + picklist → 默认 prompt → picklist 不在 prompt 允许集合 → 报错。"""
    field_type, cfg = _base("picklist", mode=None)
    assert "mode" not in cfg
    with pytest.raises(AiFillError, match="not allowed"):
        validate_ai_fill(field_type, cfg, [])


# ── output_type 放宽（可选，不再强制 == field_type）───────────────────────────

def test_output_type_present_but_not_equal_field_type_ok():
    """output_type 仍校验 ∈ AI_OUTPUT_TYPES，但不再强制 == field_type。"""
    # currency 字段，output_type 写 number（coerce 类别），应通过
    cfg = {"mode": "web_agent", "mission": "x", "output_spec": "y", "output_type": "number"}
    validate_ai_fill("currency", cfg, [])


def test_output_type_invalid_still_raises():
    """output_type 有值但不在 AI_OUTPUT_TYPES → 仍报错。"""
    cfg = {"mode": "web_agent", "mission": "x", "output_spec": "y", "output_type": "richtext"}
    with pytest.raises(AiFillError, match="output_type"):
        validate_ai_fill("text", cfg, [])


def test_no_output_type_ok():
    """output_type 缺失 → 不报错（由后续推导）。"""
    cfg = {"mode": "summarize", "mission": "摘要", "output_spec": "返回文字"}
    validate_ai_fill("text", cfg, [])


# ── inputs.fields 校验保留 ────────────────────────────────────────────────────

def test_inputs_fields_unknown_still_raises():
    cfg = {"mode": "summarize", "mission": "x", "output_spec": "y",
           "inputs": {"fields": ["nonexistent"]}}
    with pytest.raises(AiFillError, match="unknown field"):
        validate_ai_fill("text", cfg, ["name", "industry"])


def test_inputs_fields_known_ok():
    cfg = {"mode": "summarize", "mission": "x", "output_spec": "y",
           "inputs": {"fields": ["industry"]}}
    validate_ai_fill("text", cfg, ["name", "industry"])
