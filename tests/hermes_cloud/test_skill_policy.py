"""系统默认 CRM 技能保护策略(向导/技能面只增不减)。

规则:名字以 ``crm-`` 开头的、部署到 profile 的技能是产品核心闭环的一部分,
任何调用方(SPA 向导、Profile 设置、直接 POST)都不能把它们置为 disabled;
其余技能照旧可自由开关。
"""
from hermes_cloud.app.skill_policy import is_default_crm_skill, resolve_disabled


def test_crm_prefixed_names_are_default() -> None:
    assert is_default_crm_skill("crm-analyst")
    assert is_default_crm_skill("crm-daily-brief")
    assert not is_default_crm_skill("crm")          # 无连字符,非前缀命中
    assert not is_default_crm_skill("crmx-tool")
    assert not is_default_crm_skill("ascii-art")


def test_resolve_disabled_never_disables_default_crm_skills() -> None:
    togglable = {"crm-analyst", "crm-coach", "ascii-art", "ideation"}
    # 调用方试图只保留 ideation —— crm-* 不进 disabled,ascii-art 进。
    disabled = resolve_disabled(togglable, desired_enabled={"ideation"})
    assert disabled == {"ascii-art"}


def test_resolve_disabled_normal_skills_still_toggle() -> None:
    togglable = {"ascii-art", "ideation"}
    assert resolve_disabled(togglable, desired_enabled=set()) == {"ascii-art", "ideation"}
    assert resolve_disabled(togglable, desired_enabled={"ascii-art"}) == {"ideation"}


def test_resolve_disabled_ignores_unknown_names() -> None:
    togglable = {"crm-analyst", "ascii-art"}
    disabled = resolve_disabled(togglable, desired_enabled={"ghost-skill"})
    # ghost-skill 不在 togglable,忽略;ascii-art 未被要求启用 → disabled。
    assert disabled == {"ascii-art"}
