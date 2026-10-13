"""``/api/search`` — Charter §一原语 ② full-text search projection.

The legacy form full-text-searched per-machine ``state.db`` sqlite via
FTS5. Now it queries ``interactions.search_text`` (alembic 0027 GIN
tsvector index) filtered to the calling user's actor. These tests pin:

* Auth required.
* Empty / whitespace query → 400.
* Mocked PG rows surface in the response shape (session_id /
  message_id / role / snippet / timestamp / rank).
* DB unavailable → empty + error field, never 500.
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
    app = create_app(AppConfig(api_keys={"dev-key": "1"}))
    return TestClient(app)


def _mock_db_session(*, rows=None):
    fake_db = MagicMock()
    fake_db.fetchall.return_value = rows or []
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes.crm.db.db_session", return_value=cm)


def test_search_requires_auth(client):
    resp = client.get("/api/search?q=hello")
    assert resp.status_code == 401


def test_search_empty_query_returns_400(client):
    resp = client.get("/api/search?q=",
                      headers={"Authorization": "Bearer dev-key"})
    assert resp.status_code == 400


def test_search_whitespace_query_returns_400(client):
    resp = client.get("/api/search?q=%20%20",
                      headers={"Authorization": "Bearer dev-key"})
    assert resp.status_code == 400


def test_search_returns_pg_results(client):
    """Endpoint reads from interactions.search_text via tsvector;
    mock returns one row → response surfaces it in the SPA shape."""
    rows = [{
        "interaction_id": "uuid-1",
        "occurred_at": dt.datetime(2026, 4, 25, 10, 0, tzinfo=dt.timezone.utc),
        "rank": 0.95,
        "content": {
            "session_id": "conv_abc123",
            "message_id": "msg-1",
            "role": "user",
            "text": "帮我解释量子纠缠到底是什么意思",
        },
    }]
    with _mock_db_session(rows=rows):
        resp = client.get("/api/search?q=量子纠缠",
                          headers={"Authorization": "Bearer dev-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    r = data["results"][0]
    assert r["session_id"] == "conv_abc123"
    assert r["message_id"] == "msg-1"
    assert r["role"] == "user"
    assert "量子纠缠" in r["snippet"]
    assert r["rank"] == 0.95


def test_search_no_results_returns_empty(client):
    with _mock_db_session(rows=[]):
        resp = client.get("/api/search?q=没人说过这句话",
                          headers={"Authorization": "Bearer dev-key"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_db_unavailable_returns_empty_with_error(client):
    cm = MagicMock()
    cm.__enter__ = MagicMock(side_effect=RuntimeError("CRM DB down"))
    cm.__exit__ = MagicMock(return_value=False)
    with patch("hermes.crm.db.db_session", return_value=cm):
        resp = client.get("/api/search?q=anything",
                          headers={"Authorization": "Bearer dev-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert "error" in data


def test_search_limit_capped_at_50(client):
    """The ``limit`` query param is bounded to [1, 50] so a malicious
    abusive value can't OOM the DB by streaming the entire tsvector
    matchset."""
    with _mock_db_session(rows=[]) as p:
        resp = client.get("/api/search?q=hi&limit=999",
                          headers={"Authorization": "Bearer dev-key"})
    assert resp.status_code == 200
    db_mock = p.return_value.__enter__.return_value
    fetchall_args = db_mock.fetchall.call_args.args
    # The capped LIMIT is the 4th positional arg in the params tuple.
    assert fetchall_args[1][3] == 50
