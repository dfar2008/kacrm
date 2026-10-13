"""
TDD: 4 模式策略表 ModeSpec
"""
import pytest
from hermes_cloud.app.crm.ai_modes import ModeSpec, mode_spec


class TestModeSpec:
    def test_classify_fields(self):
        spec = mode_spec("classify")
        assert spec.needs_graph is True
        assert spec.needs_web is False
        assert spec.coerce_kind == "option"

    def test_web_agent_needs_web(self):
        spec = mode_spec("web_agent")
        assert spec.needs_web is True

    def test_summarize_fields(self):
        spec = mode_spec("summarize")
        assert spec.needs_graph is True
        assert spec.coerce_kind == "text"

    def test_prompt_no_web(self):
        spec = mode_spec("prompt")
        assert spec.needs_web is False

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            mode_spec("nonexistent_mode")

    def test_all_instruction_templates_nonempty(self):
        for m in ("classify", "summarize", "web_agent", "prompt"):
            spec = mode_spec(m)
            assert spec.instruction_template, f"mode={m} 的 instruction_template 不能为空"

    def test_mode_spec_returns_modespec(self):
        for m in ("classify", "summarize", "web_agent", "prompt"):
            assert isinstance(mode_spec(m), ModeSpec)

    def test_frozen_immutable(self):
        spec = mode_spec("classify")
        with pytest.raises((AttributeError, TypeError)):
            spec.needs_web = True  # type: ignore
