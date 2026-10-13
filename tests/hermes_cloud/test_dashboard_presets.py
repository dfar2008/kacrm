"""Preset structure tests + DSL compilability check."""
from __future__ import annotations

import pathlib

import pytest

from hermes_cloud.app.dashboards.compiler import compile_dsl
from hermes_cloud.app.dashboards.dsl import DSLQuery
from hermes_cloud.app.dashboards.presets import presets_for_role
from hermes_cloud.app.dashboards.semantic.registry import Registry


REG = Registry.from_directory(
    pathlib.Path("hermes_cloud/app/dashboards/semantic")
)
ALL_GRANTS = {"see_revenue", "see_team_member_performance",
              "see_org_overview", "see_hr_signals"}


def test_employee_has_one_tab():
    out = presets_for_role("employee")
    assert len(out) == 1
    assert out[0].key == "employee.my_work"
    assert out[0].title == "我的工作"
    # Chart count is allowed to grow as the my_work preset adds
    # panels (currently 8 — was 3 in the original spec). Assert a
    # floor so a future trim that drops below the original spec
    # surfaces, but don't pin the exact number.
    assert len(out[0].charts) >= 3


def test_manager_has_two_tabs_team_dashboard_first():
    out = presets_for_role("manager")
    assert len(out) == 2
    assert out[0].key == "manager.team"
    assert out[0].title == "团队看板"
    # 5-panel original spec; now expanded. Same floor-assertion
    # pattern as employee.my_work above.
    assert len(out[0].charts) >= 5


def test_admin_has_three_tabs_org_first():
    out = presets_for_role("admin")
    assert len(out) == 3
    assert out[0].key == "admin.org"
    assert out[0].title == "组织全景"


def test_unknown_role_empty():
    assert presets_for_role("bogus") == []


@pytest.mark.parametrize("role", ["employee", "manager", "admin"])
def test_all_preset_dsls_validate(role):
    """Every preset DSL must parse + compile under full grants.

    This is the migration acceptance gate (spec §10.c) at the structural
    level — actual data parity needs a live PG to verify, but if a DSL
    won't even compile then it's broken before we get there.
    """
    for tab in presets_for_role(role):
        for ch in tab.charts:
            q = DSLQuery(**ch.dsl)
            out = compile_dsl(q, registry=REG, caller_id=1, grants=ALL_GRANTS)
            assert out.sql.startswith("SELECT")
