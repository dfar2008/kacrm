"""Trust decisions audit endpoint — routing + scoping contract.

The real query joins trust_decisions → gateway_actions and a users
lookup, which means an integration harness would need a full DB. At
this layer we monkeypatch the auth + db_session so the tests stay
cheap and pin:

  - filters are forwarded into the SQL args
  - non-admins get the profile-ownership clause appended
  - admins don't
  - datetime values are stringified for JSON
  - summary aggregation is correct
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cloud.app.trust import api as trust_api


class FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None
        self.last_args = None

    def fetchall(self, sql, args):
        self.last_sql = sql
        self.last_args = args
        return self._rows


@contextmanager
def _db_session(rows):
    yield FakeDb(rows)


def _install(monkeypatch, *, rows, is_admin: bool, user_id: int = 7):
    captured_db = {"db": None}

    @contextmanager
    def fake_session():
        db = FakeDb(rows)
        captured_db["db"] = db
        yield db

    monkeypatch.setattr(trust_api, "db_session", fake_session)
    monkeypatch.setattr(
        trust_api,
        "require_dashboard_auth",
        lambda req, cfg: (user_id, "tok"),
    )

    # Patch UsersDb.get_user_by_id via importing inside the endpoint.
    import hermes.crm.models.users_db as users_db_mod

    class FakeUsersDb:
        def __init__(self, _db):
            pass

        def get_user_by_id(self, uid):
            return {"id": uid, "is_admin": is_admin}

    monkeypatch.setattr(users_db_mod, "UsersDb", FakeUsersDb)

    app = FastAPI()
    app.include_router(trust_api.router)
    return TestClient(app), captured_db


def _row(**over):
    base = {
        "id": 1,
        "action_id": "a-1",
        "trust_level_snapshot": 2,
        "decision": "auto_execute",
        "reason": "trust level 2 — risk=low",
        "decided_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        "agent_name": "field_extractor",
        "action_kind": "update",
        "profile_id": "p-1",
        "action_state": "executed",
        "target": {"module": "potentials", "id": 5},
    }
    base.update(over)
    return base


def test_admin_sees_all_and_no_profile_filter(monkeypatch):
    client, cap = _install(
        monkeypatch,
        rows=[_row(), _row(id=2, decision="require_approval")],
        is_admin=True,
    )
    r = client.get("/api/trust/decisions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    # Admin query should NOT contain the profile-ownership clause.
    assert cap["db"] is not None
    assert "profiles WHERE user_id" not in cap["db"].last_sql
    # Summary aggregates
    assert body["summary"]["by_decision"]["auto_execute"] == 1
    assert body["summary"]["by_decision"]["require_approval"] == 1
    assert body["summary"]["by_agent"]["field_extractor"] == 2
    # datetime stringified
    assert isinstance(body["data"][0]["decided_at"], str)


def test_non_admin_gets_profile_ownership_clause(monkeypatch):
    client, cap = _install(monkeypatch, rows=[], is_admin=False, user_id=42)
    r = client.get("/api/trust/decisions")
    assert r.status_code == 200
    assert "profiles WHERE user_id" in cap["db"].last_sql
    # args include the user_id for the subselect
    assert 42 in cap["db"].last_args


def test_filters_forwarded(monkeypatch):
    client, cap = _install(monkeypatch, rows=[], is_admin=True)
    r = client.get(
        "/api/trust/decisions",
        params={
            "profile_id": "p-1",
            "agent_name": "field_extractor",
            "action_kind": "update",
            "decision": "auto_execute",
            "limit": 50,
            "offset": 10,
        },
    )
    assert r.status_code == 200
    sql = cap["db"].last_sql
    assert "ga.profile_id = %s" in sql
    assert "ga.agent_name = %s" in sql
    assert "ga.kind = %s" in sql
    assert "td.decision = %s" in sql
    args = list(cap["db"].last_args)
    # first four are the filter values
    assert args[:4] == ["p-1", "field_extractor", "update", "auto_execute"]
    # last two are limit + offset
    assert args[-2:] == [50, 10]


def test_limit_bounded(monkeypatch):
    client, _ = _install(monkeypatch, rows=[], is_admin=True)
    # over-cap
    r = client.get("/api/trust/decisions", params={"limit": 9999})
    assert r.status_code == 422  # Query le=500


def test_empty_rows_returns_empty_summary(monkeypatch):
    client, _ = _install(monkeypatch, rows=[], is_admin=True)
    r = client.get("/api/trust/decisions")
    body = r.json()
    assert body["count"] == 0
    assert body["summary"] == {"by_decision": {}, "by_agent": {}}
