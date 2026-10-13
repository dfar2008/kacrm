"""``/api/sessions`` — Charter §定律 4 single-source reads.

The endpoint reads ``agent_sessions`` directly (the single source of
truth in charter v2 §定律 4 — the legacy ``session_store`` /
``agent_session_metrics`` join was retired). These tests pin:

* Auth required.
* Empty store → empty list.
* Mocked ``agent_sessions`` rows surface in the response shape the
  SPA expects (id/title/preview/source/message_count/updated_at).
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


@pytest.fixture
def client():
    app = create_app(AppConfig(api_keys={"test-key": "1"}))
    return TestClient(app)


def _mock_db_session(*, session_rows=None):
    """Patch crm.db.db_session so list_sessions' ``agent_sessions``
    query returns the supplied rows."""
    fake_db = MagicMock()
    fake_db.fetchall.return_value = session_rows or []
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes.crm.db.db_session", return_value=cm)


def test_sessions_requires_auth(client):
    resp = client.get("/api/sessions")
    assert resp.status_code == 401


def test_sessions_empty_returns_empty_list(client):
    """``agent_sessions`` empty → []."""
    with _mock_db_session():
        resp = client.get("/api/sessions",
                          headers={"Authorization": "Bearer test-key"})
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_sessions_surfaces_agent_session_rows(client):
    """An ``agent_sessions`` row surfaces in the SPA response shape.

    The query aliases columns to ``conversation_id`` / ``source`` /
    ``started_at`` / ``last_active_at`` (the latter two already passed
    through ``to_timestamp`` so the row carries datetimes)."""
    sessions = [{
        "conversation_id": "agent-sess-1",
        "source": "api_server",
        "raw_user_id": 1,
        "message_count": 5,
        "chat_name": None,
        "thread_id": None,
        "first_user_content": "hello there",
        "last_content": "final reply",
        "started_at":     dt.datetime(2026, 4, 25, 10, 0, tzinfo=dt.timezone.utc),
        "last_active_at": dt.datetime(2026, 4, 25, 10, 30, tzinfo=dt.timezone.utc),
    }]
    with _mock_db_session(session_rows=sessions):
        resp = client.get("/api/sessions",
                          headers={"Authorization": "Bearer test-key"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    row = data[0]
    assert row["id"] == "agent-sess-1"
    # ``api_server`` (hermes' OpenAI-compat HTTP server) normalizes to the
    # SPA-facing ``web`` surface.
    assert row["source"] == "web"
    assert row["message_count"] == 5
    assert row["title"] == "hello there"
    assert row["preview"] == "final reply"
    assert row["updated_at"].startswith("2026-04-25T10:30")


def test_sessions_db_unavailable_returns_empty(client):
    """CRM DB down: warn + return []. Does not 500.

    (fail-fast Gate 1 in commit 11 will prevent boot if PG is unreachable
    in production, but the endpoint itself stays defensive.)"""
    cm = MagicMock()
    cm.__enter__ = MagicMock(side_effect=RuntimeError("CRM DB down"))
    cm.__exit__ = MagicMock(return_value=False)
    with patch("hermes.crm.db.db_session", return_value=cm):
        resp = client.get("/api/sessions",
                          headers={"Authorization": "Bearer test-key"})
    assert resp.status_code == 200
    assert resp.json()["data"] == []
