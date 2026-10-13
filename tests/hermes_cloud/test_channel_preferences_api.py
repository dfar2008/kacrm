"""Tests for ``/api/me/preferences/channel_push`` GET / PUT (Phase 4
v2.2 §4.4 + §10.3).

We split into two layers:

* ``apply_patch`` is pure — covered by parametric unit tests against
  the dataclass directly. No FastAPI / DB needed.
* The HTTP routes are covered with ``TestClient`` against a freshly
  built FastAPI app whose ``get_db`` dependency is replaced by an
  in-memory fake. This avoids dragging the real psycopg pool into the
  test runner (which the conftest stubs out anyway).
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from hermes_cloud.app.channel.preferences_api import (
    ALLOWED_CHANNELS,
    apply_patch,
    prefs_to_json,
    register_routes,
)
from hermes_cloud.app.channel.store import UserChannelPrefs


# ──────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────


def _defaults(**over) -> UserChannelPrefs:
    base = UserChannelPrefs(user_id=7, channel="feishu")
    for k, v in over.items():
        setattr(base, k, v)
    return base


def test_apply_patch_unspecified_fields_are_preserved():
    """Missing fields in the patch keep their old values — this is the
    contract callers rely on for partial updates."""
    base = _defaults(
        push_today_priority=False, max_per_day=5,
        timezone="UTC",
    )
    out = apply_patch(base, {"push_weekly": False})
    assert out.push_weekly is False
    # Untouched fields retained.
    assert out.push_today_priority is False
    assert out.max_per_day == 5
    assert out.timezone == "UTC"


def test_apply_patch_boolean_coercion():
    base = _defaults()
    out = apply_patch(base, {
        "push_today_priority": False,
        "push_weekly": False,
        "prefer_team_brief": True,
    })
    assert out.push_today_priority is False
    assert out.push_weekly is False
    assert out.prefer_team_brief is True


def test_apply_patch_severity_validation():
    base = _defaults()
    out = apply_patch(base, {"push_alert_min_sev": "low"})
    assert out.push_alert_min_sev == "low"
    with pytest.raises(ValueError):
        apply_patch(base, {"push_alert_min_sev": "ultra"})


def test_apply_patch_quiet_hours_range():
    base = _defaults()
    out = apply_patch(base, {
        "quiet_hours_start": 23, "quiet_hours_end": 7,
    })
    assert out.quiet_hours_start == 23
    assert out.quiet_hours_end == 7
    with pytest.raises(ValueError):
        apply_patch(base, {"quiet_hours_start": 25})
    with pytest.raises(ValueError):
        apply_patch(base, {"quiet_hours_end": -1})
    with pytest.raises(ValueError):
        apply_patch(base, {"quiet_hours_start": "morning"})


def test_apply_patch_max_per_day_range():
    base = _defaults()
    out = apply_patch(base, {"max_per_day": 10})
    assert out.max_per_day == 10
    with pytest.raises(ValueError):
        apply_patch(base, {"max_per_day": -1})
    with pytest.raises(ValueError):
        apply_patch(base, {"max_per_day": 999})


def test_apply_patch_timezone_validates_iana():
    base = _defaults()
    out = apply_patch(base, {"timezone": "America/New_York"})
    assert out.timezone == "America/New_York"
    with pytest.raises(ValueError):
        apply_patch(base, {"timezone": "Asia/Shangai"})  # typo
    with pytest.raises(ValueError):
        apply_patch(base, {"timezone": ""})


def test_apply_patch_muted_signals_normalized():
    base = _defaults()
    out = apply_patch(base, {
        "muted_signals": ["sig:overdue", "  ", "sig:negotiation_stalled"],
    })
    # Empty / whitespace entries dropped.
    assert out.muted_signals == ["sig:overdue", "sig:negotiation_stalled"]
    with pytest.raises(ValueError):
        apply_patch(base, {"muted_signals": "not-a-list"})
    with pytest.raises(ValueError):
        apply_patch(base, {"muted_signals": ["x"] * 201})


def test_apply_patch_rejects_unknown_field():
    """Typos in the JSON payload should be loud — silently ignoring
    them lets a buggy SPA think it saved something it didn't."""
    base = _defaults()
    with pytest.raises(ValueError) as e:
        apply_patch(base, {"push_alert_minsev": "low"})  # typo
    assert "unknown" in str(e.value).lower()


def test_apply_patch_returns_new_instance():
    """Pure helper: must not mutate the input dataclass."""
    base = _defaults(max_per_day=3)
    out = apply_patch(base, {"max_per_day": 10})
    assert out.max_per_day == 10
    assert base.max_per_day == 3  # unchanged


def test_prefs_to_json_shape():
    p = _defaults(
        push_today_priority=False, push_alert_min_sev="low",
        muted_signals=["sig:x"], prefer_team_brief=True,
    )
    j = prefs_to_json(p)
    assert j["channel"] == "feishu"
    assert j["push_today_priority"] is False
    assert j["push_alert_min_sev"] == "low"
    assert j["muted_signals"] == ["sig:x"]
    assert j["prefer_team_brief"] is True
    # updated_at is None on a fresh defaulted dataclass — must serialise
    # cleanly (some clients break on undefined keys).
    assert "updated_at" in j
    assert j["updated_at"] is None


# ──────────────────────────────────────────────────────────────────
# Route layer (TestClient)
# ──────────────────────────────────────────────────────────────────


class _FakeDb:
    """In-memory stand-in for ``DbWrapper``; only implements what
    ``store.get_prefs`` / ``store.upsert_prefs`` actually call."""

    def __init__(self):
        self.rows: Dict[tuple, Dict[str, Any]] = {}

    def fetchone(self, sql: str, params: tuple) -> Dict[str, Any] | None:
        # Routes only hit fetchone via get_prefs; key by (user_id, channel).
        if "user_channel_prefs" in sql and "WHERE user_id" in sql:
            user_id, channel = int(params[0]), str(params[1])
            return self.rows.get((user_id, channel))
        return None

    def execute(self, sql: str, params: tuple) -> None:
        if "INSERT INTO user_channel_prefs" in sql:
            from datetime import datetime, timezone
            (
                user_id, channel,
                pp, pw, pmin, qhs, qhe, mpd, tz, muted_json, ptb,
                ddpe,  # decisions_daily_push_enabled (alembic 0056)
            ) = params
            import json as _json
            self.rows[(int(user_id), str(channel))] = {
                "user_id": int(user_id),
                "channel": str(channel),
                "push_today_priority": bool(pp),
                "push_weekly": bool(pw),
                "push_alert_min_sev": str(pmin),
                "quiet_hours_start": int(qhs),
                "quiet_hours_end": int(qhe),
                "max_per_day": int(mpd),
                "timezone": str(tz),
                "muted_signals": _json.loads(muted_json),
                "prefer_team_brief": bool(ptb),
                "decisions_daily_push_enabled": ddpe,
                "updated_at": datetime.now(tz=timezone.utc),
            }


@pytest.fixture
def client_and_db(monkeypatch):
    """Build a minimal FastAPI app with the prefs router mounted, an
    in-memory DAO, and a stubbed auth middleware that yields user_id=7."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    fake_db = _FakeDb()

    # Stub the auth resolver before mounting routes — preferences_api
    # imports require_bearer_auth lazily, so we can monkeypatch the
    # whole helper to a known user_id.
    import hermes_cloud.app.channel.preferences_api as mod
    monkeypatch.setattr(
        mod, "_user_id_from_request", lambda request, app: 7,
    )

    # Replace get_db dependency with our fake.
    from hermes.crm.db import get_db
    app = FastAPI()
    app.state.config = MagicMock()
    register_routes(app)
    app.dependency_overrides[get_db] = lambda: fake_db

    return TestClient(app), fake_db


def test_get_returns_defaults_for_missing_row(client_and_db):
    """Stage A safety net: GET on a channel with no prefs row must NOT
    materialize the row, just hand back defaults."""
    client, db = client_and_db
    r = client.get("/api/me/preferences/channel_push?channel=feishu")
    assert r.status_code == 200
    body = r.json()
    assert body["prefs"]["channel"] == "feishu"
    assert body["prefs"]["push_today_priority"] is True  # default
    assert body["prefs"]["updated_at"] is None
    # Critical: GET must not write.
    assert db.rows == {}


def test_get_no_channel_returns_all_whitelisted(client_and_db):
    client, _db = client_and_db
    r = client.get("/api/me/preferences/channel_push")
    assert r.status_code == 200
    body = r.json()
    assert "channels" in body
    for ch in ALLOWED_CHANNELS:
        assert ch in body["channels"]
        assert body["channels"][ch]["channel"] == ch


def test_get_unknown_channel_422(client_and_db):
    client, _db = client_and_db
    r = client.get("/api/me/preferences/channel_push?channel=signal")
    assert r.status_code == 422


def test_put_partial_update_preserves_other_fields(client_and_db):
    client, db = client_and_db
    # First PUT — only flips push_weekly off.
    r = client.put(
        "/api/me/preferences/channel_push",
        json={"channel": "feishu", "push_weekly": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prefs"]["push_weekly"] is False
    # Untouched defaults preserved.
    assert body["prefs"]["push_today_priority"] is True
    assert body["prefs"]["max_per_day"] == 3
    # Second PUT — patch a different field; previous patch must persist.
    r = client.put(
        "/api/me/preferences/channel_push",
        json={"channel": "feishu", "max_per_day": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prefs"]["push_weekly"] is False  # still off
    assert body["prefs"]["max_per_day"] == 5


def test_put_invalid_severity_returns_422(client_and_db):
    client, _db = client_and_db
    r = client.put(
        "/api/me/preferences/channel_push",
        json={"channel": "feishu", "push_alert_min_sev": "ultra"},
    )
    assert r.status_code == 422


def test_put_invalid_timezone_returns_422(client_and_db):
    client, _db = client_and_db
    r = client.put(
        "/api/me/preferences/channel_push",
        json={"channel": "feishu", "timezone": "Mars/Olympus"},
    )
    assert r.status_code == 422


def test_put_unknown_channel_returns_422(client_and_db):
    client, _db = client_and_db
    r = client.put(
        "/api/me/preferences/channel_push",
        json={"channel": "carrier-pigeon", "max_per_day": 1},
    )
    assert r.status_code == 422


def test_put_missing_channel_returns_422(client_and_db):
    client, _db = client_and_db
    r = client.put(
        "/api/me/preferences/channel_push", json={"max_per_day": 1},
    )
    assert r.status_code == 422


def test_put_then_get_round_trip(client_and_db):
    client, db = client_and_db
    r = client.put(
        "/api/me/preferences/channel_push",
        json={
            "channel": "ilink",
            "push_today_priority": False,
            "quiet_hours_start": 21,
            "quiet_hours_end": 9,
            "muted_signals": ["sig:overdue"],
        },
    )
    assert r.status_code == 200, r.text
    # Row should now exist.
    assert (7, "ilink") in db.rows
    # GET reflects what we saved.
    r = client.get("/api/me/preferences/channel_push?channel=ilink")
    assert r.status_code == 200
    p = r.json()["prefs"]
    assert p["push_today_priority"] is False
    assert p["quiet_hours_start"] == 21
    assert p["quiet_hours_end"] == 9
    assert p["muted_signals"] == ["sig:overdue"]
    assert p["updated_at"] is not None


def test_put_unauthenticated_returns_401(monkeypatch):
    """When neither bearer nor cookie auth resolves, the 401 surfaces
    cleanly (not a 500)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.state.config = MagicMock()
    # Don't override _user_id_from_request — let the real one run and
    # short-circuit through both auth fallbacks (none configured).
    register_routes(app)

    class _Db:
        def fetchone(self, *a, **k): return None
        def execute(self, *a, **k): pass

    from hermes.crm.db import get_db
    app.dependency_overrides[get_db] = lambda: _Db()

    client = TestClient(app)
    r = client.put(
        "/api/me/preferences/channel_push",
        json={"channel": "feishu", "max_per_day": 1},
    )
    assert r.status_code == 401
