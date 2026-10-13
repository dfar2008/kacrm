"""``/api/analytics`` — Charter §一原语 ③ projection-table reads.

The legacy form of this endpoint read per-machine ``state.db`` sqlite.
Now it queries the ``agent_session_metrics`` projection (alembic 0030's
trigger keeps it in sync with interactions). These tests pin:

* Auth required (no token → 401).
* Empty PG → empty structure (not 500). DB-unavailable also → empty +
  error field, not crash.
* Mocked PG with rows → fields propagate to the response shape the SPA
  consumes (``daily``, ``by_model``, ``totals``).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


@pytest.fixture
def client():
    app = create_app(AppConfig(api_keys={"dev-key": "dev"}))
    return TestClient(app)


def _mock_db_session(*, daily=None, by_model=None, totals=None):
    """Patch crm.db.db_session to return a fake DbWrapper that the
    analytics endpoint can call fetchall/fetchone on."""
    fake_db = MagicMock()
    fake_db.fetchall.side_effect = [daily or [], by_model or []]
    fake_db.fetchone.return_value = totals or {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "reasoning_tokens": 0,
        "estimated_cost_usd": 0.0, "sessions": 0,
    }
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes.crm.db.db_session", return_value=cm)


def test_analytics_requires_auth(client):
    resp = client.get("/api/analytics?days=30")
    assert resp.status_code == 401


def test_analytics_returns_structure(client):
    """Endpoint reads from agent_session_metrics; mock returns one daily
    row + one by-model row + populated totals → response surfaces them."""
    daily = [{
        "day": "2026-04-25",
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 200, "reasoning_tokens": 0,
        "estimated_cost_usd": 0.05, "sessions": 3,
    }]
    by_model = [{
        "model": "gpt-4",
        "input_tokens": 1000, "output_tokens": 500,
        "estimated_cost_usd": 0.05, "sessions": 3,
    }]
    totals = {
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 200, "reasoning_tokens": 0,
        "estimated_cost_usd": 0.05, "sessions": 3,
    }
    with _mock_db_session(daily=daily, by_model=by_model, totals=totals):
        resp = client.get(
            "/api/analytics?days=30",
            headers={"Authorization": "Bearer dev-key"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["daily"] == daily
    assert data["by_model"] == by_model
    assert data["totals"]["sessions"] == 3
    assert data["totals"]["input_tokens"] == 1000
    assert data["totals"]["estimated_cost_usd"] == 0.05


def test_analytics_empty_pg_returns_empty_structure(client):
    """No rows in agent_session_metrics → daily=[], by_model=[], totals=0."""
    with _mock_db_session():  # all empty
        resp = client.get(
            "/api/analytics?days=7",
            headers={"Authorization": "Bearer dev-key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["daily"] == []
    assert data["by_model"] == []
    assert data["totals"]["sessions"] == 0


def test_analytics_db_unavailable_returns_empty_with_error(client):
    """If db_session itself raises (CRM DB down), we still surface a 200
    with the empty structure + error field — the SPA can render the
    skeleton + show a transient "no data" indicator without crashing."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(side_effect=RuntimeError("CRM DB down"))
    cm.__exit__ = MagicMock(return_value=False)
    with patch("hermes.crm.db.db_session", return_value=cm):
        resp = client.get(
            "/api/analytics?days=30",
            headers={"Authorization": "Bearer dev-key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["daily"] == []
    assert data["totals"]["sessions"] == 0
    assert "error" in data


def test_analytics_days_clamped(client):
    """days param is clamped to [1, 365] — abusive values shouldn't
    explode the SQL interval expression."""
    with _mock_db_session() as p:
        resp = client.get(
            "/api/analytics?days=99999",
            headers={"Authorization": "Bearer dev-key"},
        )
    assert resp.status_code == 200
    # Inspect the SQL params: should have used '365' for the clamped days.
    db_mock = p.return_value.__enter__.return_value
    fetchall_calls = db_mock.fetchall.call_args_list
    assert fetchall_calls
    days_param = fetchall_calls[0].args[1][1]  # second arg, second positional
    assert days_param == "365"
