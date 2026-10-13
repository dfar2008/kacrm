"""Unit tests for dashboards.grants — pure Python, no DB."""
from __future__ import annotations

import pytest

from hermes_cloud.app.dashboards.grants import (
    GRANT_NAMES,
    ROLE_DEFAULT_GRANTS,
    grants_for_user,
    has_grant,
)


class _FakeDb:
    """Minimal stub of hermes.crm.db.DbWrapper for test isolation."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self, sql, args=None):
        # We don't validate SQL shape here; tests assume the call is for
        # user_grants. The args tuple's first element is user_id.
        assert "user_grants" in sql
        return list(self._rows)


def test_grant_names_complete():
    assert GRANT_NAMES == {
        "see_revenue",
        "see_team_member_performance",
        "see_org_overview",
        "see_hr_signals",
    }


def test_role_defaults():
    assert ROLE_DEFAULT_GRANTS["employee"] == set()
    assert ROLE_DEFAULT_GRANTS["manager"] == {
        "see_revenue", "see_team_member_performance",
    }
    assert ROLE_DEFAULT_GRANTS["admin"] == {
        "see_revenue", "see_team_member_performance",
        "see_org_overview", "see_hr_signals",
    }


def test_grants_for_user_role_defaults_only():
    db = _FakeDb([])
    out = grants_for_user(db, user_id=1, role="manager")
    assert out == {"see_revenue", "see_team_member_performance"}


def test_grants_for_user_merges_explicit():
    db = _FakeDb([{"grant_name": "see_hr_signals"}])
    out = grants_for_user(db, user_id=1, role="manager")
    assert out == {"see_revenue", "see_team_member_performance", "see_hr_signals"}


def test_grants_for_user_unknown_role_falls_back_to_explicit_only():
    db = _FakeDb([{"grant_name": "see_revenue"}])
    out = grants_for_user(db, user_id=1, role="contractor")
    assert out == {"see_revenue"}


def test_grants_for_user_with_no_db():
    out = grants_for_user(None, user_id=1, role="admin")
    assert out == ROLE_DEFAULT_GRANTS["admin"]


def test_has_grant_true_via_role_default():
    db = _FakeDb([])
    assert has_grant(db, user_id=1, role="manager", grant="see_revenue") is True


def test_has_grant_true_via_explicit():
    db = _FakeDb([{"grant_name": "see_hr_signals"}])
    assert has_grant(db, user_id=1, role="manager", grant="see_hr_signals") is True


def test_has_grant_false():
    db = _FakeDb([])
    assert has_grant(db, user_id=1, role="manager", grant="see_org_overview") is False


def test_has_grant_unknown_grant_raises():
    with pytest.raises(ValueError, match="unknown grant"):
        has_grant(_FakeDb([]), user_id=1, role="admin", grant="see_anything")
