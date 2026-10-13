"""Service-layer unit tests (no DB)."""
from __future__ import annotations

import uuid

import pytest

from hermes_cloud.app.dashboards import service
from hermes_cloud.app.dashboards.store import DashboardTab


def _tab(**kw) -> DashboardTab:
    base = dict(
        id=uuid.uuid4(), owner_user_id=10, title="t", position=0,
        visibility="private", shared_with={},
        is_system_preset=False, preset_key=None, title_i18n=None,
        created_at=None, updated_at=None,
    )
    base.update(kw)
    return DashboardTab(**base)


# ---- visibility ------------------------------------------------------------


def test_owner_can_view_and_edit():
    t = _tab(owner_user_id=10)
    assert service.can_view_tab(t, viewer_id=10, viewer_dept_id=None) is True
    assert service.can_edit_tab(t, viewer_id=10) is True


def test_other_user_cannot_view_private():
    t = _tab(owner_user_id=10, visibility="private")
    assert service.can_view_tab(t, viewer_id=20, viewer_dept_id=99) is False


def test_shared_users_grant_view_only():
    t = _tab(visibility="shared",
             shared_with={"users": [20], "everyone": False})
    assert service.can_view_tab(t, viewer_id=20, viewer_dept_id=99) is True
    assert service.can_edit_tab(t, viewer_id=20) is False  # only owner edits


def test_shared_dept_grant_view():
    t = _tab(visibility="shared",
             shared_with={"departments": [5], "everyone": False})
    assert service.can_view_tab(t, viewer_id=20, viewer_dept_id=5) is True
    assert service.can_view_tab(t, viewer_id=20, viewer_dept_id=6) is False


def test_shared_everyone():
    t = _tab(visibility="shared", shared_with={"everyone": True})
    assert service.can_view_tab(t, viewer_id=999, viewer_dept_id=None) is True


def test_shared_no_match():
    t = _tab(visibility="shared",
             shared_with={"users": [], "departments": [], "everyone": False})
    assert service.can_view_tab(t, viewer_id=20, viewer_dept_id=99) is False


# ---- role resolution -------------------------------------------------------


def test_resolve_role_admin_wins():
    assert service.resolve_role(is_admin=True, has_reports=False) == "admin"
    assert service.resolve_role(is_admin=True, has_reports=True) == "admin"


def test_resolve_role_manager():
    assert service.resolve_role(is_admin=False, has_reports=True) == "manager"


def test_resolve_role_employee():
    assert service.resolve_role(is_admin=False, has_reports=False) == "employee"
