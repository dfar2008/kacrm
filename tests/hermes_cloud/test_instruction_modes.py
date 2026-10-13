"""
TDD: _build_instruction 按 4 模式 + _effective_output_type coerce 推导。
M1-T5 cloud 部分。
"""
import json
import pytest

from hermes_cloud.app.crm.ai_attributes import (
    _build_instruction,
    _effective_output_type,
)
from hermes_cloud.app.crm.ai_modes import mode_spec


# ─── helpers ─────────────────────────────────────────────────────────────────

def _field_meta(mode="prompt", output_type=None, mission="完成任务", output_spec="输出结果"):
    ai_fill = {
        "enabled": True,
        "mode": mode,
        "mission": mission,
        "output_spec": output_spec,
        "context_rules": "",
    }
    if output_type is not None:
        ai_fill["output_type"] = output_type
    return {
        "field_name": "test_field",
        "field_type": "text",
        "field_config": {"ai_fill": ai_fill},
    }


def _build(mode, context=None, output_type=None, mission="完成任务"):
    fm = _field_meta(mode=mode, output_type=output_type, mission=mission)
    ctx = context or {}
    return _build_instruction(fm, ctx)


# ─── web_agent 模式 ─────────────────────────────────────────────────────────

class TestWebAgentInstruction:
    def test_contains_web_search(self):
        instruction = _build("web_agent")
        assert "web_search" in instruction

    def test_contains_source_url(self):
        instruction = _build("web_agent")
        assert "来源 URL" in instruction

    def test_contains_abstained_on_miss(self):
        instruction = _build("web_agent")
        assert "abstained=true" in instruction

    def test_mode_label_in_instruction(self):
        instruction = _build("web_agent")
        assert "[模式:web_agent]" in instruction

    def test_web_hint_at_end_before_contract(self):
        """web_agent hint 在正文内，CONTRACT 在最末。"""
        instruction = _build("web_agent")
        web_pos = instruction.index("web_search")
        contract_pos = instruction.index("返回严格 JSON")
        assert web_pos < contract_pos


# ─── classify 模式 ──────────────────────────────────────────────────────────

class TestClassifyInstruction:
    def test_contains_instruction_template(self):
        spec = mode_spec("classify")
        # instruction_template 含"分类助手"
        instruction = _build("classify")
        assert "分类" in instruction or "数据分类" in instruction

    def test_mode_label_present(self):
        instruction = _build("classify")
        assert "[模式:classify]" in instruction

    def test_no_web_hint(self):
        instruction = _build("classify")
        assert "web_search" not in instruction


# ─── summarize 模式 ─────────────────────────────────────────────────────────

class TestSummarizeInstruction:
    def test_mode_label_present(self):
        instruction = _build("summarize")
        assert "[模式:summarize]" in instruction

    def test_no_web_hint(self):
        instruction = _build("summarize")
        assert "web_search" not in instruction


# ─── prompt 模式（旧配置向后兼容）─────────────────────────────────────────

class TestPromptModeBackwardCompat:
    def test_no_mode_defaults_to_prompt(self):
        """旧配置无 mode 字段，instruction 不报错，含原有 parts。"""
        fm = {
            "field_name": "legacy_field",
            "field_type": "text",
            "field_config": {"ai_fill": {
                "enabled": True,
                "mission": "旧版使命",
                "output_spec": "返回文本",
                "context_rules": "无特殊规则",
            }},
        }
        instruction = _build_instruction(fm, {})
        assert "旧版使命" in instruction
        assert "[使命]" in instruction
        assert "[输出要求]" in instruction  # output_spec 退役后由系统自动生成
        assert "[模式:prompt]" in instruction

    def test_fields_in_instruction(self):
        ctx = {"fields": {"name": "测试公司", "industry": "科技"}}
        instruction = _build("prompt", context=ctx)
        assert "测试公司" in instruction

    def test_contract_appended(self):
        instruction = _build("prompt")
        assert "返回严格 JSON" in instruction


# ─── semantic / subgraph 注入 ─────────────────────────────────────────────

class TestContextInjection:
    def test_semantic_injected(self):
        ctx = {
            "fields": {},
            "semantic": [{"id": 1, "content": "客户偏好高端产品"}],
        }
        instruction = _build("classify", context=ctx)
        assert "[相关记忆]" in instruction
        assert "客户偏好高端产品" in instruction

    def test_subgraph_injected(self):
        ctx = {
            "fields": {},
            "subgraph": {"entities": [{"id": 10, "name": "母公司"}], "edges": []},
        }
        instruction = _build("summarize", context=ctx)
        assert "[实体关系图]" in instruction
        assert "母公司" in instruction

    def test_semantic_absent_no_section(self):
        ctx = {"fields": {}}
        instruction = _build("prompt", context=ctx)
        assert "[相关记忆]" not in instruction

    def test_subgraph_absent_no_section(self):
        ctx = {"fields": {}}
        instruction = _build("prompt", context=ctx)
        assert "[实体关系图]" not in instruction

    def test_semantic_truncated_at_4000(self):
        """semantic 大数据时 JSON 截断到 4000 字以内。"""
        huge = [{"id": i, "content": "x" * 200} for i in range(100)]
        ctx = {"fields": {}, "semantic": huge}
        instruction = _build("classify", context=ctx)
        # 定位 [相关记忆] 段
        start = instruction.index("[相关记忆]")
        # 找下一个换行后内容（以 [ 开头的 JSON 部分）
        segment = instruction[start:start + 4200]
        assert len(segment) <= 4200  # 留余量

    def test_subgraph_truncated_at_3000(self):
        huge = {"entities": [{"id": i, "name": "n" * 100} for i in range(50)], "edges": []}
        ctx = {"fields": {}, "subgraph": huge}
        instruction = _build("summarize", context=ctx)
        start = instruction.index("[实体关系图]")
        segment = instruction[start:start + 3200]
        assert len(segment) <= 3200

    def test_rendered_prompt_used_over_mission(self):
        """context 有 rendered_prompt 时，使命段用渲染后版本。"""
        ctx = {
            "fields": {},
            "rendered_prompt": "已渲染:目标公司是 阿里巴巴",
        }
        fm = _field_meta(mode="prompt", mission="原始使命 {company_name}")
        instruction = _build_instruction(fm, ctx)
        assert "已渲染:目标公司是 阿里巴巴" in instruction


# ─── _effective_output_type ──────────────────────────────────────────────────

class TestEffectiveOutputType:
    def test_explicit_output_type_wins(self):
        """显式 output_type 优先于 coerce_kind。"""
        spec = mode_spec("classify")  # coerce_kind=option → picklist
        af = {"output_type": "text"}  # 显式覆盖
        assert _effective_output_type(af, spec) == "text"

    def test_classify_no_output_type_gives_picklist(self):
        """classify coerce_kind=option → 推导为 picklist。"""
        spec = mode_spec("classify")
        af = {}
        assert _effective_output_type(af, spec) == "picklist"

    def test_summarize_no_output_type_gives_text(self):
        spec = mode_spec("summarize")
        af = {}
        assert _effective_output_type(af, spec) == "text"

    def test_prompt_no_output_type_gives_text(self):
        spec = mode_spec("prompt")
        af = {}
        assert _effective_output_type(af, spec) == "text"

    def test_web_agent_no_output_type_gives_text(self):
        """web_agent coerce_kind=text → 推导为 text（不是 number）。"""
        spec = mode_spec("web_agent")
        af = {}
        assert _effective_output_type(af, spec) == "text"

    def test_web_agent_with_explicit_number(self):
        """web_agent 字段若显式配置 output_type=number，应保留 number。"""
        spec = mode_spec("web_agent")
        af = {"output_type": "number"}
        assert _effective_output_type(af, spec) == "number"

    def test_empty_string_output_type_falls_back(self):
        """output_type 空串视为未配置，走 coerce_kind 推导。"""
        spec = mode_spec("classify")
        af = {"output_type": ""}
        assert _effective_output_type(af, spec) == "picklist"
