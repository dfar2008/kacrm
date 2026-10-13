"""Role-based preset tabs (spec §9).

Lazy-materialized on first /app/dashboards visit. Marked
is_system_preset=True with preset_key='<role>.<key>' for traceability;
any subsequent op.apply (spec §9.3) clears the flag (auto-fork).

Design rationale (Monday-style, calibrated to real fill rates):

  * Real data has ``actual_close_date`` always NULL and ``latest_contact_date``
    always NULL — any time-series chart keyed on close-date is dead on
    arrival. So all trend / leaderboard charts pivot to ``created_at``,
    which IS reliably filled, and we label them as "新增商机" not "成交"
    where appropriate.
  * Dimension charts (status / milestone / industry / department) filter
    out NULL so "未填写" doesn't dominate the bar / pie.
  * Root department "Company" (id=1) is excluded from per-department
    aggregates — it's the org root, not a real team.
  * "高价值在跑商机" uses the deal-name dimension, not owner — so the
    chart actually shows individual top deals, matching the title.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional


@dataclasses.dataclass(frozen=True)
class PresetChart:
    title: str
    dsl: Dict[str, Any]


@dataclasses.dataclass(frozen=True)
class PresetTab:
    key: str
    # Multi-locale tab title. Resolved at read time by ``routes._resolved_tab_title``
    # using the request's current locale. Stored in the DB as
    # ``dashboard_tabs.title_i18n`` (JSONB). When a user manually
    # renames a preset tab via the SPA, ``title_i18n`` is cleared and
    # only the raw ``title`` column is kept — user-chosen names beat
    # bilingual presets.
    title_i18n: Dict[str, str]
    charts: tuple[PresetChart, ...]

    @property
    def title(self) -> str:
        """Default-locale snapshot for the legacy single-string ``title``
        column. Preset materialization writes this into
        ``dashboard_tabs.title`` so any code path that doesn't yet
        know about ``title_i18n`` still gets a sensible value."""
        return self.title_i18n.get("zh-CN") or next(iter(self.title_i18n.values()), "")


# =============================================================================
# Money / pipeline metric cards
# =============================================================================


def _open_pipeline_amount(scope: str, title: str) -> Dict[str, Any]:
    """Sum of amount over opportunities NOT in won/lost — i.e. the
    current pipeline. Doesn't depend on close-date filling."""
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [],
        "filters": [
            {"dim": "status", "op": "is_not_null", "value": None},
            {"dim": "status", "op": "neq", "value": "won"},
            {"dim": "status", "op": "neq", "value": "lost"},
        ],
        "chart": {"type": "metric", "title": title},
    }


def _won_amount_this_month(scope: str, title: str) -> Dict[str, Any]:
    """Won deals created this month. We can't filter on close-date
    (always null) so use created_at — close enough for KPI purposes."""
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [],
        "filters": [
            {"dim": "status", "op": "eq", "value": "won"},
            {"dim": "created_at", "op": "gte", "value": "@start_of_month"},
        ],
        "chart": {"type": "metric", "title": title},
    }


def _new_opps_amount_this_month(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [],
        "filters": [
            {"dim": "created_at", "op": "gte", "value": "@start_of_month"},
        ],
        "chart": {"type": "metric", "title": title},
    }


def _open_opportunities(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "count"}],
        "dimensions": [],
        "filters": [
            {"dim": "status", "op": "is_not_null", "value": None},
            {"dim": "status", "op": "neq", "value": "won"},
            {"dim": "status", "op": "neq", "value": "lost"},
        ],
        "chart": {"type": "metric", "title": title},
    }


def _stuck_opportunities(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "stuck_count"}],
        "dimensions": [],
        "filters": [],
        "chart": {"type": "metric", "title": title},
    }


def _new_customers_this_month(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "customer", "scope": scope,
        "measures": [{"name": "count"}],
        "dimensions": [],
        "filters": [
            {"dim": "created_at", "op": "gte", "value": "@start_of_month"},
        ],
        "chart": {"type": "metric", "title": title},
    }


def _total_customers(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "customer", "scope": scope,
        "measures": [{"name": "count"}],
        "dimensions": [],
        "filters": [],
        "chart": {"type": "metric", "title": title},
    }


# =============================================================================
# Task / activity metric cards (personal view)
# =============================================================================


def _my_done_tasks_this_week() -> Dict[str, Any]:
    """Tasks I closed this week — uses updated_at because enddate is
    sparsely filled; updated_at advances when status flips to done."""
    return {
        "entity": "task", "scope": "self",
        "measures": [{"name": "done_count"}],
        "dimensions": [],
        "filters": [
            {"dim": "updated_at", "op": "gte", "value": "@7_days_ago"},
        ],
        "chart": {"type": "metric", "title": "本周完成任务数"},
    }


def _my_open_tasks_count() -> Dict[str, Any]:
    return {
        "entity": "task", "scope": "self",
        "measures": [{"name": "count"}],
        "dimensions": [],
        "filters": [
            {"dim": "status", "op": "neq", "value": "done"},
        ],
        "chart": {"type": "metric", "title": "我的待办任务数"},
    }


def _my_open_tasks_by_priority() -> Dict[str, Any]:
    return {
        "entity": "task", "scope": "self",
        "measures": [{"name": "count"}],
        "dimensions": [{"name": "priority"}],
        "filters": [
            {"dim": "status", "op": "neq", "value": "done"},
            {"dim": "priority", "op": "is_not_null", "value": None},
        ],
        "order_by": [{"measure": "count", "dir": "desc"}],
        "chart": {"type": "bar", "title": "我的待办任务（按优先级）",
                  "x": "priority", "y": "count"},
    }


# =============================================================================
# Pipeline / leaderboard charts
# =============================================================================


def _pipeline_funnel(scope: str) -> Dict[str, Any]:
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "count"}, {"name": "total_amount"}],
        "dimensions": [{"name": "milestone"}],
        "filters": [
            {"dim": "milestone", "op": "is_not_null", "value": None},
        ],
        "order_by": [{"measure": "count", "dir": "desc"}],
        "chart": {"type": "funnel", "title": "商机漏斗",
                  "x": "milestone", "y": "count", "y2": "total_amount"},
    }


def _top_deals_by_name(scope: str, title: str, limit: int = 10) -> Dict[str, Any]:
    """Top open opportunities by amount — name dimension so each bar
    is a single deal, not aggregated by owner/department."""
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [{"name": "name"}],
        "filters": [
            {"dim": "status", "op": "is_not_null", "value": None},
            {"dim": "status", "op": "neq", "value": "won"},
            {"dim": "status", "op": "neq", "value": "lost"},
        ],
        "order_by": [{"measure": "total_amount", "dir": "desc"}],
        "limit": limit,
        "chart": {"type": "bar", "title": title,
                  "x": "name", "y": "total_amount"},
    }


def _workload_by_owner(scope: str, title: str) -> Dict[str, Any]:
    """In-flight opps per owner — Monday-style workload visibility."""
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "count"}],
        "dimensions": [{"name": "owner_user_id"}],
        "filters": [
            {"dim": "status", "op": "is_not_null", "value": None},
            {"dim": "status", "op": "neq", "value": "won"},
            {"dim": "status", "op": "neq", "value": "lost"},
        ],
        "order_by": [{"measure": "count", "dir": "desc"}],
        "limit": 10,
        "chart": {"type": "bar", "title": title,
                  "x": "owner_user_id", "y": "count"},
    }


def _new_opps_leaderboard_30d(scope: str, title: str) -> Dict[str, Any]:
    """Salesperson leaderboard by NEW opportunity amount over last
    30 days — pivots off created_at, not close-date."""
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [{"name": "owner_user_id"}],
        "filters": [
            {"dim": "created_at", "op": "gte", "value": "@30_days_ago"},
        ],
        "order_by": [{"measure": "total_amount", "dir": "desc"}],
        "limit": 10,
        "chart": {"type": "bar", "title": title,
                  "x": "owner_user_id", "y": "total_amount"},
    }


def _monthly_new_opps_trend(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "opportunity", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [{"name": "created_at", "granularity": "month"}],
        "filters": [
            {"dim": "created_at", "op": "gte", "value": "@start_of_year"},
        ],
        "chart": {"type": "line", "title": title,
                  "x": "created_at", "y": "total_amount"},
    }


def _monthly_new_customers_trend(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "customer", "scope": scope,
        "measures": [{"name": "count"}],
        "dimensions": [{"name": "created_at", "granularity": "month"}],
        "filters": [
            {"dim": "created_at", "op": "gte", "value": "@start_of_year"},
        ],
        "chart": {"type": "line", "title": title,
                  "x": "created_at", "y": "count"},
    }


def _customer_industry_pie(scope: str) -> Dict[str, Any]:
    return {
        "entity": "customer", "scope": scope,
        "measures": [{"name": "count"}],
        "dimensions": [{"name": "industry"}],
        "filters": [
            {"dim": "industry", "op": "is_not_null", "value": None},
        ],
        "order_by": [{"measure": "count", "dir": "desc"}],
        "limit": 15,
        "chart": {"type": "pie", "title": "客户行业分布",
                  "x": "industry", "y": "count"},
    }


def _department_pipeline_value() -> Dict[str, Any]:
    """Per-department pipeline value, restricted to real teams.

    Excludes ``department_id IS NULL`` (no team) and ``department_id = 1``
    (the seeded "Company" root — every tenant gets this row as the
    parent of all real departments; rolling it into the bar would
    double-count children's amounts as the parent's). The hardcoded
    ``1`` reflects a seed-flow invariant: the Company root is always
    inserted first and gets id=1. If a tenant ever reseeds without
    Company, this filter no-ops (which is the safe failure mode —
    chart shows one extra row).

    The DSL doesn't support subqueries, so we can't dynamically
    resolve "the row where parent_id IS NULL" via a join here.
    Revisit if reseed semantics ever change.
    """
    return {
        "entity": "opportunity", "scope": "org",
        "measures": [{"name": "total_amount"}],
        "dimensions": [{"name": "department_id"}],
        "filters": [
            {"dim": "department_id", "op": "is_not_null", "value": None},
            {"dim": "department_id", "op": "neq", "value": 1},
        ],
        "order_by": [{"measure": "total_amount", "dir": "desc"}],
        "chart": {"type": "bar", "title": "各部门商机金额",
                  "x": "department_id", "y": "total_amount"},
    }


# =============================================================================
# Order / receivable / payment / product cards
# =============================================================================


def _orders_this_month(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "order", "scope": scope,
        "measures": [{"name": "count"}],
        "dimensions": [],
        "filters": [
            {"dim": "orderdate", "op": "gte", "value": "@start_of_month"},
        ],
        "chart": {"type": "metric", "title": title},
    }


def _order_amount_this_month(scope: str, title: str) -> Dict[str, Any]:
    return {
        "entity": "order", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [],
        "filters": [
            {"dim": "orderdate", "op": "gte", "value": "@start_of_month"},
        ],
        "chart": {"type": "metric", "title": title},
    }


def _outstanding_receivables(scope: str) -> Dict[str, Any]:
    return {
        "entity": "receivable", "scope": scope,
        "measures": [{"name": "total_due"}],
        "dimensions": [],
        "filters": [],
        "chart": {"type": "metric", "title": "应收账款总额"},
    }


def _payment_this_month(scope: str) -> Dict[str, Any]:
    return {
        "entity": "payment", "scope": scope,
        "measures": [{"name": "total_amount"}],
        "dimensions": [],
        "filters": [
            {"dim": "paydate", "op": "gte", "value": "@start_of_month"},
        ],
        "chart": {"type": "metric", "title": "本月已收款"},
    }


def _total_products() -> Dict[str, Any]:
    return {
        "entity": "product", "scope": "org",
        "measures": [{"name": "count"}],
        "dimensions": [],
        "filters": [],
        "chart": {"type": "metric", "title": "产品总数"},
    }


def _low_stock_products() -> Dict[str, Any]:
    return {
        "entity": "product", "scope": "org",
        "measures": [{"name": "low_stock_count"}],
        "dimensions": [],
        "filters": [],
        "chart": {"type": "metric", "title": "库存预警（低于安全库存）"},
    }


# =============================================================================
# Tab compositions
# =============================================================================


def _my_work_charts() -> tuple[PresetChart, ...]:
    """Personal Monday-style mix: task + pipeline + customer view.
    Closes out useless "我的客户阶段分布" (most accounts have null status)
    and "近 30 天我的报价" (sparse) in favor of task / activity widgets."""
    return (
        PresetChart(title="我的在跑商机金额",
                    dsl=_open_pipeline_amount("self", "我的在跑商机金额")),
        PresetChart(title="进行中的商机",
                    dsl=_open_opportunities("self", "进行中的商机")),
        PresetChart(title="本月新增客户",
                    dsl=_new_customers_this_month("self", "本月新增客户")),
        PresetChart(title="本周完成任务数",
                    dsl=_my_done_tasks_this_week()),
        PresetChart(title="我的待办任务数",
                    dsl=_my_open_tasks_count()),
        PresetChart(title="我的待办任务（按优先级）",
                    dsl=_my_open_tasks_by_priority()),
        PresetChart(title="高价值在跑商机 Top 10",
                    dsl=_top_deals_by_name("self", "高价值在跑商机 Top 10")),
        PresetChart(title="月度新增商机金额走势",
                    dsl=_monthly_new_opps_trend("self", "月度新增商机金额走势")),
    )


def _team_dashboard_charts() -> tuple[PresetChart, ...]:
    return (
        PresetChart(title="本月团队新增商机金额",
                    dsl=_new_opps_amount_this_month("team", "本月团队新增商机金额")),
        PresetChart(title="本月团队成交金额",
                    dsl=_won_amount_this_month("team", "本月团队成交金额")),
        PresetChart(title="本月已收款",
                    dsl=_payment_this_month("team")),
        PresetChart(title="沉睡商机数",
                    dsl=_stuck_opportunities("team", "沉睡商机数")),
        PresetChart(title="商机漏斗",
                    dsl=_pipeline_funnel("team")),
        PresetChart(title="团队成员工作量（在跑商机数）",
                    dsl=_workload_by_owner("team", "团队成员工作量（在跑商机数）")),
        PresetChart(title="销售员新增商机金额 · 30 天",
                    dsl=_new_opps_leaderboard_30d("team", "销售员新增商机金额 · 30 天")),
        PresetChart(title="高价值在跑商机 Top 10",
                    dsl=_top_deals_by_name("team", "高价值在跑商机 Top 10")),
        PresetChart(title="月度新增商机金额走势",
                    dsl=_monthly_new_opps_trend("team", "月度新增商机金额走势")),
        PresetChart(title="客户行业分布",
                    dsl=_customer_industry_pie("team")),
    )


def _org_overview_charts() -> tuple[PresetChart, ...]:
    """组织全景 — admin's whole-organization view (scope=org)."""
    return (
        PresetChart(title="本月公司新增商机金额",
                    dsl=_new_opps_amount_this_month("org", "本月公司新增商机金额")),
        PresetChart(title="本月订单金额",
                    dsl=_order_amount_this_month("org", "本月订单金额")),
        PresetChart(title="应收账款总额",
                    dsl=_outstanding_receivables("org")),
        PresetChart(title="客户总数",
                    dsl=_total_customers("org", "客户总数")),
        PresetChart(title="产品总数",
                    dsl=_total_products()),
        PresetChart(title="库存预警",
                    dsl=_low_stock_products()),
        PresetChart(title="商机漏斗",
                    dsl=_pipeline_funnel("org")),
        PresetChart(title="销售员新增商机金额 · 30 天",
                    dsl=_new_opps_leaderboard_30d("org", "销售员新增商机金额 · 30 天")),
        PresetChart(title="各部门商机金额",
                    dsl=_department_pipeline_value()),
        PresetChart(title="月度新增商机金额走势",
                    dsl=_monthly_new_opps_trend("org", "月度新增商机金额走势")),
        PresetChart(title="月度新增客户走势",
                    dsl=_monthly_new_customers_trend("org", "月度新增客户走势")),
        PresetChart(title="客户行业分布",
                    dsl=_customer_industry_pie("org")),
    )


# Preset tab title catalog — three unique strings across all roles.
# Keep this dict the single source of truth so a re-translation only
# touches one place; the backfill migration also imports this map by
# preset_key prefix to refill ``title_i18n`` for already-materialized
# tabs in existing user accounts.
_PRESET_TAB_TITLES: Dict[str, Dict[str, str]] = {
    "my_work": {"zh-CN": "我的工作",   "en-US": "My Work"},
    "team":    {"zh-CN": "团队看板",   "en-US": "Team Dashboard"},
    "org":     {"zh-CN": "组织全景",   "en-US": "Organization Overview"},
}


def preset_title_i18n_for_key(preset_key: str) -> Optional[Dict[str, str]]:
    """Lookup the bilingual title for a stored ``preset_key``.

    ``preset_key`` is ``<role>.<suffix>`` (e.g. ``admin.team``); the
    suffix is the stable identifier we translate. Returns ``None`` for
    unknown keys — caller treats that as "user-customized, leave alone".
    """
    if not preset_key or "." not in preset_key:
        return None
    suffix = preset_key.rsplit(".", 1)[1]
    return _PRESET_TAB_TITLES.get(suffix)


def presets_for_role(role: str) -> List[PresetTab]:
    my_work = _PRESET_TAB_TITLES["my_work"]
    team = _PRESET_TAB_TITLES["team"]
    org = _PRESET_TAB_TITLES["org"]
    if role == "employee":
        return [PresetTab(key="employee.my_work", title_i18n=my_work,
                          charts=_my_work_charts())]
    if role == "manager":
        return [
            PresetTab(key="manager.team", title_i18n=team,
                      charts=_team_dashboard_charts()),
            PresetTab(key="manager.my_work", title_i18n=my_work,
                      charts=_my_work_charts()),
        ]
    if role == "admin":
        return [
            PresetTab(key="admin.org", title_i18n=org,
                      charts=_org_overview_charts()),
            PresetTab(key="admin.team", title_i18n=team,
                      charts=_team_dashboard_charts()),
            PresetTab(key="admin.my_work", title_i18n=my_work,
                      charts=_my_work_charts()),
        ]
    return []
