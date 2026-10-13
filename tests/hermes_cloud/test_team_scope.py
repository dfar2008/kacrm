"""Tests for ``app.scope.team_scope`` (Phase 5.1)."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from hermes_cloud.app.scope import viewable_user_ids
from hermes_cloud.app.scope.audit import record_scope_access


class _FakeDb:
    def __init__(self, *,
                 is_admin: bool = False,
                 team_rows: list = None,
                 all_active: list = None,
                 raise_on_team: bool = False):
        self.is_admin = is_admin
        self.team_rows = team_rows or []
        self.all_active = all_active or []
        self.raise_on_team = raise_on_team
        self.audit_inserts = []

    def fetchone(self, sql, params=()):
        if "is_admin FROM users" in sql:
            return {"is_admin": self.is_admin}
        return None

    def fetchall(self, sql, params=()):
        if "managed_dept" in sql:
            if self.raise_on_team:
                raise RuntimeError("simulated query failure")
            return self.team_rows
        if "WHERE deleted = FALSE" in sql and "managed_dept" not in sql:
            return self.all_active
        return []

    def execute(self, sql, params=()):
        if "INSERT INTO audit_log" in sql:
            self.audit_inserts.append(params)

    def rollback(self):
        pass


# ──────────────────────────────────────────────────────────────────
# viewable_user_ids
# ──────────────────────────────────────────────────────────────────


def test_self_scope_returns_caller_only():
    assert viewable_user_ids(_FakeDb(), caller_id=7, scope="self") == {7}


def test_team_scope_returns_subordinates_plus_caller():
    db = _FakeDb(team_rows=[{"id": 7}, {"id": 11}, {"id": 12}])
    out = viewable_user_ids(db, caller_id=7, scope="team")
    assert out == {7, 11, 12}


def test_team_scope_falls_back_to_self_on_query_failure():
    """Plan v1.2 §3 / §8: scope query failure must NOT raise into the
    read path — fall back to self-only and let the caller continue."""
    db = _FakeDb(raise_on_team=True)
    out = viewable_user_ids(db, caller_id=7, scope="team")
    assert out == {7}


def test_team_scope_for_non_manager_collapses_to_self():
    """Non-manager calling team-scope should collapse to {self} since
    the recursive CTE finds no managed_dept rows; caller still in
    set via the OR clause."""
    db = _FakeDb(team_rows=[{"id": 7}])  # only caller, no subordinates
    assert viewable_user_ids(db, caller_id=7, scope="team") == {7}


def test_org_scope_admin_returns_all_active_users():
    db = _FakeDb(
        is_admin=True,
        all_active=[{"id": 1}, {"id": 2}, {"id": 7}, {"id": 11}],
    )
    out = viewable_user_ids(db, caller_id=1, scope="org")
    assert out == {1, 2, 7, 11}


def test_org_scope_non_admin_auto_downgrades_to_team():
    """Plan v1.2 §3: non-admin can't elevate to org. Auto-fall to team
    (no error, no privilege escalation)."""
    db = _FakeDb(is_admin=False, team_rows=[{"id": 7}, {"id": 11}])
    out = viewable_user_ids(db, caller_id=7, scope="org")
    # Should be {7, 11} from team-scope query, not org-scope expansion
    assert out == {7, 11}


# ──────────────────────────────────────────────────────────────────
# audit_log writer
# ──────────────────────────────────────────────────────────────────


def test_record_scope_access_writes_one_row():
    db = _FakeDb()
    record_scope_access(
        db, caller_id=7, scope_kind="team",
        resource_kind="surface_dispatch",
        resource_filter={"surface": "feishu"},
    )
    assert len(db.audit_inserts) == 1
    params = db.audit_inserts[0]
    assert params[0] == 7
    assert params[1] == "team"
    assert params[2] == "surface_dispatch"
    assert "feishu" in params[3]


def test_record_scope_access_swallows_errors():
    """Audit failure must not propagate — never block read path."""
    class _CrashDb:
        def execute(self, *a, **kw):
            raise RuntimeError("PG down")
        def rollback(self):
            pass

    # Should not raise
    record_scope_access(
        _CrashDb(), caller_id=1, scope_kind="team",
        resource_kind="x",
    )
