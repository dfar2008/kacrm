"""系统默认 CRM 技能保护策略——单一事实源。

产品规则:部署到 profile 的 ``crm-*`` 技能是 CRM 核心闭环(建档/教练/晨报/
自动化…)的组成部分,用户在任何面(快速向导、Profile 设置、直接调 API)都
只能**增配**技能,不能移除系统默认的 CRM 技能。

GET /api/user-config/skills 用 :func:`is_default_crm_skill` 给条目打
``locked`` 标记(前端据此隐藏「移除」);POST 用 :func:`resolve_disabled`
计算落盘的 disabled 集合,保证 crm-* 永不进 disabled——前端被绕过也拦得住。
"""
from __future__ import annotations

_DEFAULT_PREFIX = "crm-"


def is_default_crm_skill(name: str) -> bool:
    """名字以 ``crm-`` 开头的技能视为系统默认 CRM 技能(不可移除)。"""
    return name.startswith(_DEFAULT_PREFIX)


def resolve_disabled(togglable: set[str], desired_enabled: set[str]) -> set[str]:
    """由「可开关全集」与「调用方想启用的集合」算出应落盘的 disabled 集合。

    - 不在 ``togglable`` 里的名字忽略(磁盘上不存在);
    - 系统默认 CRM 技能永不进 disabled,无论调用方是否列出。
    """
    desired = desired_enabled & togglable
    return {
        name for name in togglable - desired
        if not is_default_crm_skill(name)
    }
