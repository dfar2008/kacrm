"""Tests for ``POST /api/copilot/scoped/send`` (P1, retire-mini-assistants).

The scoped endpoint reuses the same dispatch transport as
``/crm/send`` but is driven by ``scope`` + ``context`` instead of
``(module, record_id)``. These tests pin:

* scope=schema → SurfaceKind.SPA_SCHEMA_ASSISTANT, preamble mentions
  crm_schema + the module name, return shape correct.
* scope=dashboard → SurfaceKind.SPA_DASHBOARD_CHAT, preamble mentions
  crm_dashboard + the tab id.
* missing context (no module / no tab_id) → 400.
* invalid scope → 400.

Mirrors ``test_copilot_routes_dispatch.py``: TestClient + patched
module-level globals, since the handler is a closure inside
``register_routes`` and isn't importable directly.
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


def _build_app(*, dispatch_fn):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from hermes_cloud.app.copilot import routes as r

    app = FastAPI()
    app.state.config = MagicMock(internal_key="k")
    app.state.config_store = MagicMock()
    app.dependency_overrides[r._require_user] = (
        lambda: {"id": 7, "is_admin": False}
    )
    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=dispatch_fn,
    ), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ):
        r.register_routes(app)
        return TestClient(app)


def _capture_dispatch(captured: Dict[str, Any]):
    async def _fake(*, app, envelope, **kw):
        captured["surface"] = envelope.surface
        # The user message is the last message in the list.
        captured["user_text"] = envelope.messages[-1]["content"]
        captured["model"] = kw.get("body_extras", {}).get("model")
        return MagicMock(
            status_code=200, reply_text="ok reply",
            tool_executions=[{"name": "crm_schema"}], usage={},
            model="big-pickle", session_id="s", raw_body=b"{}",
        )

    return _fake


def test_schema_scope_uses_schema_surface_and_preamble():
    captured: Dict[str, Any] = {}
    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=_capture_dispatch(captured),
    ), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ):
        client = _build_app(dispatch_fn=_capture_dispatch(captured))
        resp = client.post(
            "/api/copilot/scoped/send",
            json={
                "scope": "schema",
                "context": {"module": "accounts"},
                "message": "加一个 ICP 等级字段",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback"] is False
    assert body["reply"] == "ok reply"
    assert body["tool_executions"] == [{"name": "crm_schema"}]

    from hermes_cloud.app.surface import SurfaceKind
    assert captured["surface"] == SurfaceKind.SPA_SCHEMA_ASSISTANT
    text = captured["user_text"]
    assert isinstance(text, str)
    assert "crm_schema" in text
    assert "accounts" in text
    assert "加一个 ICP 等级字段" in text
    assert captured["model"] == "big-pickle"


def test_dashboard_scope_uses_dashboard_surface_and_preamble():
    captured: Dict[str, Any] = {}
    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=_capture_dispatch(captured),
    ), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ):
        client = _build_app(dispatch_fn=_capture_dispatch(captured))
        resp = client.post(
            "/api/copilot/scoped/send",
            json={
                "scope": "dashboard",
                "context": {"tab_id": "tab-42"},
                "message": "加一个漏斗图",
            },
        )

    assert resp.status_code == 200, resp.text
    from hermes_cloud.app.surface import SurfaceKind
    assert captured["surface"] == SurfaceKind.SPA_DASHBOARD_CHAT
    text = captured["user_text"]
    assert "crm_dashboard" in text
    assert "tab-42" in text
    assert "加一个漏斗图" in text


def test_schema_scope_missing_module_is_400():
    def _unused(**kw):
        raise AssertionError("dispatch should not be reached")

    client = _build_app(dispatch_fn=_unused)
    resp = client.post(
        "/api/copilot/scoped/send",
        json={"scope": "schema", "context": {}, "message": "hi"},
    )
    assert resp.status_code == 400


def test_dashboard_scope_missing_tab_id_is_400():
    def _unused(**kw):
        raise AssertionError("dispatch should not be reached")

    client = _build_app(dispatch_fn=_unused)
    resp = client.post(
        "/api/copilot/scoped/send",
        json={"scope": "dashboard", "context": {}, "message": "hi"},
    )
    assert resp.status_code == 400


def test_invalid_scope_is_400():
    def _unused(**kw):
        raise AssertionError("dispatch should not be reached")

    client = _build_app(dispatch_fn=_unused)
    resp = client.post(
        "/api/copilot/scoped/send",
        json={"scope": "nope", "context": {"module": "accounts"},
              "message": "hi"},
    )
    assert resp.status_code == 400


# ── Persistence (P6) ──────────────────────────────────────────────────


def test_scoped_send_persists_both_turns_under_panel_key():
    """scope=schema → both the user (chat) and assistant (copilot_reply)
    turns are emitted under the namespaced ``ai-schema:<module>`` subject
    bucket, NOT the bare module name (which the chatbox list-mode uses)."""
    captured: Dict[str, Any] = {}
    emit_calls = []

    def _emit(**kw):
        emit_calls.append(kw)

    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=_capture_dispatch(captured),
    ), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ), patch(
        "hermes_cloud.app.copilot.routes.emit", new=_emit,
    ), patch(
        "hermes_cloud.app.copilot.routes.db_session",
    ):
        client = _build_app(dispatch_fn=_capture_dispatch(captured))
        resp = client.post(
            "/api/copilot/scoped/send",
            json={
                "scope": "schema",
                "context": {"module": "accounts"},
                "message": "加一个字段",
            },
        )

    assert resp.status_code == 200, resp.text
    kinds = [c["kind"] for c in emit_calls]
    assert kinds == ["chat", "copilot_reply"]
    for c in emit_calls:
        assert c["subjects"] == [{"module": "ai-schema:accounts"}]
        assert c["actors"] == [{"type": "user", "id": 7}]
    assert emit_calls[0]["content"]["role"] == "user"
    assert emit_calls[0]["content"]["text"] == "加一个字段"
    assert emit_calls[1]["content"]["role"] == "assistant"
    assert emit_calls[1]["content"]["text"] == "ok reply"
    assert emit_calls[1]["content"]["tool_executions"] == [{"name": "crm_schema"}]


def test_scoped_send_dashboard_persists_under_tab_panel_key():
    captured: Dict[str, Any] = {}
    emit_calls = []

    def _emit(**kw):
        emit_calls.append(kw)

    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=_capture_dispatch(captured),
    ), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ), patch(
        "hermes_cloud.app.copilot.routes.emit", new=_emit,
    ), patch(
        "hermes_cloud.app.copilot.routes.db_session",
    ):
        client = _build_app(dispatch_fn=_capture_dispatch(captured))
        resp = client.post(
            "/api/copilot/scoped/send",
            json={
                "scope": "dashboard",
                "context": {"tab_id": "tab-42"},
                "message": "加一个漏斗图",
            },
        )

    assert resp.status_code == 200, resp.text
    assert [c["kind"] for c in emit_calls] == ["chat", "copilot_reply"]
    for c in emit_calls:
        assert c["subjects"] == [{"module": "ai-dashboard:tab-42"}]


# ── History (P6) ──────────────────────────────────────────────────────


def _history_app(rows):
    """TestClient whose get_db returns a stub db; its fetchall returns
    ``rows`` and records query params for assertion."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from hermes_cloud.app.copilot import routes as r
    from hermes.crm.db import get_db

    seen: Dict[str, Any] = {}

    class _Db:
        def fetchall(self, sql, params):
            seen["sql"] = sql
            seen["params"] = params
            return rows

    app = FastAPI()
    app.state.config = MagicMock(internal_key="k")
    app.state.config_store = MagicMock()
    app.dependency_overrides[r._require_user] = (
        lambda: {"id": 7, "is_admin": False}
    )
    app.dependency_overrides[get_db] = lambda: _Db()
    r.register_routes(app)
    return TestClient(app), seen


def test_scoped_history_schema_queries_panel_key_and_returns_turns():
    import datetime as _dt
    import json as _json

    rows = [
        {
            "id": 1,
            "occurred_at": _dt.datetime(2026, 1, 1, 0, 0, 0),
            "content": {"text": "加一个字段", "role": "user"},
            "source": "copilot:user",
        },
        {
            "id": 2,
            "occurred_at": _dt.datetime(2026, 1, 1, 0, 0, 1),
            "content": {
                "text": "好的",
                "role": "assistant",
                "tool_executions": [{"name": "crm_schema"}],
                "model": "big-pickle",
            },
            "source": "copilot:assistant",
        },
    ]
    client, seen = _history_app(rows)
    resp = client.get(
        "/api/copilot/scoped/history",
        params={"scope": "schema", "module": "accounts"},
    )
    assert resp.status_code == 200, resp.text
    turns = resp.json()["turns"]
    assert len(turns) == 2
    assert turns[0] == {
        "role": "user", "text": "加一个字段",
        "at": "2026-01-01T00:00:00",
    }
    assert turns[1]["role"] == "assistant"
    assert turns[1]["tool_executions"] == [{"name": "crm_schema"}]
    assert turns[1]["model"] == "big-pickle"
    assert _json.loads(seen["params"][0]) == [{"module": "ai-schema:accounts"}]
    assert _json.loads(seen["params"][1]) == [{"type": "user", "id": 7}]


def test_scoped_history_dashboard_uses_tab_panel_key():
    import json as _json

    client, seen = _history_app([])
    resp = client.get(
        "/api/copilot/scoped/history",
        params={"scope": "dashboard", "tab_id": "tab-42"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"turns": []}
    assert _json.loads(seen["params"][0]) == [{"module": "ai-dashboard:tab-42"}]


def test_scoped_history_missing_context_is_400():
    client, _ = _history_app([])
    assert client.get(
        "/api/copilot/scoped/history", params={"scope": "schema"},
    ).status_code == 400
    assert client.get(
        "/api/copilot/scoped/history", params={"scope": "dashboard"},
    ).status_code == 400
    assert client.get(
        "/api/copilot/scoped/history", params={"scope": "nope", "module": "x"},
    ).status_code == 400
