"""
TDD: AI Attributes {} 变量解析引擎
测试 hermes_cloud/app/crm/ai_variables.py 中的 parse_variables 和 render。
"""
import pytest
from hermes_cloud.app.crm.ai_variables import parse_variables, render


# ---------------------------------------------------------------------------
# parse_variables
# ---------------------------------------------------------------------------

class TestParseVariables:
    def test_own_simple(self):
        """普通字段变量 → kind=own，path=[字段名]"""
        result = parse_variables("公司 {name} 的简介")
        assert len(result) == 1
        v = result[0]
        assert v["kind"] == "own"
        assert v["path"] == ["name"]
        assert v["raw"] == "{name}"

    def test_own_chinese_field(self):
        """中文字段名 → own"""
        result = parse_variables("{客户行业}")
        assert len(result) == 1
        assert result[0]["kind"] == "own"
        assert result[0]["path"] == ["客户行业"]

    def test_relation_fullwidth_separator(self):
        """全角 › 分隔符 → kind=relation"""
        result = parse_variables("{关联商机 › 金额}")
        assert len(result) == 1
        v = result[0]
        assert v["kind"] == "relation"
        assert v["path"] == ["关联商机", "金额"]
        assert v["raw"] == "{关联商机 › 金额}"

    def test_relation_halfwidth_separator(self):
        """半角 > 分隔符 → kind=relation"""
        result = parse_variables("{关联商机 > 金额}")
        assert len(result) == 1
        v = result[0]
        assert v["kind"] == "relation"
        assert v["path"] == ["关联商机", "金额"]

    def test_derived_names(self):
        """派生变量集合中的名称 → kind=derived"""
        for name in ["最近互动", "首次互动", "互动次数", "连接强度"]:
            result = parse_variables(f"{{{name}}}")
            assert len(result) == 1
            v = result[0]
            assert v["kind"] == "derived", f"{name} 应为 derived，得到 {v['kind']}"
            assert v["path"] == [name]

    def test_derived_single(self):
        """单个派生变量"""
        result = parse_variables("{最近互动}")
        assert len(result) == 1
        assert result[0]["kind"] == "derived"
        assert result[0]["path"] == ["最近互动"]

    def test_multiple_variables(self):
        """多个变量都被抽出"""
        result = parse_variables("{name} 在 {行业} 中，{最近互动}，{关联商机 › 金额}")
        assert len(result) == 4
        kinds = [v["kind"] for v in result]
        assert kinds == ["own", "own", "derived", "relation"]

    def test_no_variables(self):
        """没有变量 → 空列表"""
        result = parse_variables("没有任何变量的普通文本")
        assert result == []

    def test_raw_preserves_original(self):
        """raw 字段保留原始 {…} 文本（包含空格）"""
        result = parse_variables("{关联商机 › 金额}")
        assert result[0]["raw"] == "{关联商机 › 金额}"

    def test_path_strips_whitespace(self):
        """path 两段都去空格"""
        result = parse_variables("{  关联商机  >  金额  }")
        assert result[0]["path"] == ["关联商机", "金额"]


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

class TestRender:
    def test_own_field_replaced(self):
        """own 变量从 record 取值替换成功"""
        rendered, basis = render("名称 {name}", record={"name": "茅台"})
        assert rendered == "名称 茅台"
        assert "field:name" in basis

    def test_own_chinese_field(self):
        """中文字段名 own 变量"""
        rendered, basis = render("{行业}", record={"行业": "白酒"})
        assert rendered == "白酒"
        assert "field:行业" in basis

    def test_relation_with_resolver(self):
        """relation 变量调用 relation_resolver，替换成功"""
        def fake_resolver(rel_field, target_field):
            if rel_field == "关联商机" and target_field == "金额":
                return "100万"
            return None

        rendered, basis = render(
            "金额：{关联商机 › 金额}",
            record={},
            relation_resolver=fake_resolver,
        )
        assert rendered == "金额：100万"
        assert "relation:关联商机.金额" in basis

    def test_relation_halfwidth_separator(self):
        """半角 > 分隔符的 relation 变量也能替换"""
        def fake_resolver(rel_field, target_field):
            return "200万"

        rendered, basis = render(
            "{关联商机 > 金额}",
            record={},
            relation_resolver=fake_resolver,
        )
        assert rendered == "200万"
        assert "relation:关联商机.金额" in basis

    def test_derived_variable(self):
        """derived 变量从 derived dict 取值"""
        rendered, basis = render(
            "{最近互动}",
            record={},
            derived={"最近互动": "2024-01-01 电话沟通"},
        )
        assert rendered == "2024-01-01 电话沟通"
        assert "derived:最近互动" in basis

    def test_missing_own_field_becomes_placeholder(self):
        """record 里没有对应字段 → 替换成 '(空)'，不计入 basis"""
        rendered, basis = render("{unknown_field}", record={})
        assert rendered == "(空)"
        assert "field:unknown_field" not in basis

    def test_missing_relation_resolver_none(self):
        """relation_resolver 为 None 时 → '(空)'，不计入 basis"""
        rendered, basis = render("{关联商机 › 金额}", record={}, relation_resolver=None)
        assert rendered == "(空)"
        assert "relation:关联商机.金额" not in basis

    def test_relation_resolver_returns_none(self):
        """resolver 返回 None → '(空)'，不计入 basis"""
        rendered, basis = render(
            "{关联商机 › 金额}",
            record={},
            relation_resolver=lambda rel, tgt: None,
        )
        assert rendered == "(空)"
        assert "relation:关联商机.金额" not in basis

    def test_missing_derived_becomes_placeholder(self):
        """derived dict 里没有对应键 → '(空)'"""
        rendered, basis = render("{最近互动}", record={}, derived={})
        assert rendered == "(空)"

    def test_derived_dict_none(self):
        """derived 参数为 None 时派生变量 → '(空)'"""
        rendered, basis = render("{连接强度}", record={}, derived=None)
        assert rendered == "(空)"

    def test_no_variables_unchanged(self):
        """没有变量的 prompt 原样返回，basis 为空"""
        rendered, basis = render("静态文本", record={})
        assert rendered == "静态文本"
        assert basis == []

    def test_multiple_variables_mixed(self):
        """多类型变量混合渲染"""
        def fake_resolver(rel_field, target_field):
            return "50万"

        rendered, basis = render(
            "{name} / {关联商机 › 金额} / {最近互动}",
            record={"name": "腾讯"},
            relation_resolver=fake_resolver,
            derived={"最近互动": "昨日拜访"},
        )
        assert rendered == "腾讯 / 50万 / 昨日拜访"
        assert "field:name" in basis
        assert "relation:关联商机.金额" in basis
        assert "derived:最近互动" in basis

    def test_basis_length_only_resolved(self):
        """basis 只包含成功解析的变量，缺失的不计入"""
        rendered, basis = render(
            "{name} {missing}",
            record={"name": "华为"},
        )
        assert rendered == "华为 (空)"
        assert len(basis) == 1
        assert "field:name" in basis
