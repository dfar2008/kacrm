"""Unit tests for dashboards.scope — pure logic."""
from __future__ import annotations

import pytest

from hermes_cloud.app.dashboards.scope import (
    ScopeError,
    allowed_scopes_for_role,
    default_scope_for_role,
    resolve_scope,
)


def test_defaults_per_role():
    assert default_scope_for_role("employee") == "self"
    assert default_scope_for_role("manager") == "team"
    assert default_scope_for_role("admin") == "team"
    # unknown role falls back to most-restrictive
    assert default_scope_for_role("contractor") == "self"


def test_allowed_scopes_per_role():
    assert allowed_scopes_for_role("employee") == {"self"}
    assert allowed_scopes_for_role("manager") == {"self", "team"}
    assert allowed_scopes_for_role("admin") == {"self", "team", "org"}
    assert allowed_scopes_for_role("contractor") == {"self"}


def test_resolve_uses_default_when_requested_none():
    assert resolve_scope(role="manager", requested=None, grants=set()) == "team"


def test_resolve_employee_team_denied():
    with pytest.raises(ScopeError, match="not allowed"):
        resolve_scope(role="employee", requested="team", grants=set())


def test_resolve_manager_org_denied():
    with pytest.raises(ScopeError, match="not allowed"):
        resolve_scope(role="manager", requested="org", grants={"see_org_overview"})


def test_resolve_admin_org_requires_grant():
    with pytest.raises(ScopeError, match="see_org_overview"):
        resolve_scope(role="admin", requested="org", grants=set())
    assert resolve_scope(
        role="admin", requested="org", grants={"see_org_overview"}
    ) == "org"


def test_resolve_self_always_allowed():
    for role in ("employee", "manager", "admin"):
        assert resolve_scope(role=role, requested="self", grants=set()) == "self"
