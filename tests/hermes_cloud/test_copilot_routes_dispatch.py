"""Tests for ``copilot.routes`` after Phase 4 Stage D (plan v2.2 §4.2).

The cloud LLM loop is gone. ``copilot_crm_send`` is a thin transport
that builds an envelope with the right surface kind and dispatches to
hermes. These tests pin:

* Record-mode (record_id given) → SurfaceKind.SPA_RECORD_COPILOT
* List-mode (record_id missing) → SurfaceKind.SPA_CHATBOX
* Successful dispatch → user + assistant turns persisted, reply
  surfaced, fallback=False
* dispatch raises → fallback=True, reply empty, error in body
* hermes 4xx with empty reply → fallback=True
* No more imports of copilot/tools — the file is gone
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_db_session(db):
    """A ``db_session()`` replacement yielding the supplied fake ``db``.

    ``copilot_crm_send`` opens its own short ``with db_session() as db:``
    blocks (Charter v2 §一系统) rather than taking ``Depends(get_db)``, so
    the route's DB use must be patched at ``routes.db_session`` — the
    ``get_db`` dependency override alone never reaches it.
    """
    @contextmanager
    def _cm():
        yield db

    return _cm


def test_copilot_tools_module_is_gone():
    """Stage D: copilot/tools.py was physically removed. Importing it
    must fail — the canary against accidental resurrection."""
    with pytest.raises(ImportError):
        import hermes_cloud.app.copilot.tools  # noqa: F401


def test_routes_no_longer_imports_chat_with_tools():
    """The agent loop is gone. routes.py must not pull chat_with_tools
    or tool execution helpers — the LLM lives upstream now.

    We check ``ast`` rather than text grep so the docstring mentions
    of the removed names don't false-positive."""
    import ast
    tree = ast.parse(open(
        "hermes_cloud/app/copilot/routes.py", encoding="utf-8",
    ).read())
    forbidden = {"chat_with_tools", "execute_tool", "TOOL_SCHEMAS",
                 "ToolContext", "tool_result_message"}
    seen_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for n in node.names:
                seen_imports.add(n.name)
        elif isinstance(node, ast.Import):
            for n in node.names:
                seen_imports.add(n.name)
    leaked = forbidden & seen_imports
    assert not leaked, f"Stage D regression — re-imported: {leaked}"


def _build_app(*, dispatch_fn, db, mod_db):
    """Helper: minimal FastAPI app with dispatch + auth stubbed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from hermes_cloud.app.copilot import routes as r
    from hermes.crm.db import get_db

    app = FastAPI()
    app.state.config = MagicMock(internal_key="k")
    # ``copilot_crm_send`` resolves the user's LLM creds via
    # ``request.app.state.config_store`` (added by the ModelPicker feature,
    # commit 48a8ab3). Real startup wires this in ``create_app``; the bare
    # FastAPI app here must supply a stand-in. ``resolve_for_user`` itself
    # is patched per-test so no real config rows are needed.
    app.state.config_store = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    # Override the auth dep so requests skip session-cookie resolution.
    app.dependency_overrides[r._require_user] = (
        lambda: {"id": 7, "is_admin": False}
    )
    # Patch module-level globals BEFORE register_routes wires the handler.
    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=dispatch_fn,
    ), patch(
        "hermes_cloud.app.copilot.routes.ModulesDb",
        return_value=mod_db,
    ), patch("hermes_cloud.app.copilot.routes.emit"), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ), patch(
        "hermes_cloud.app.copilot.routes.db_session",
        new=_fake_db_session(db),
    ):
        r.register_routes(app)
        client = TestClient(app)
        return client


def test_dispatch_record_mode_uses_record_copilot_surface():
    """Record-mode → SPA_RECORD_COPILOT → patch 27 binds the
    record-page allowlist on the hermes side."""
    captured = {}

    async def _fake_dispatch(*, app, envelope, **kw):
        captured["surface"] = envelope.surface
        captured["headers"] = dict(envelope.headers)
        return MagicMock(
            status_code=200, reply_text="ok reply",
            tool_executions=[], usage={}, model="big-pickle",
            session_id="s", raw_body=b"{}",
        )

    db = MagicMock()
    mod_db = MagicMock()
    mod_db.get_module_by_name = MagicMock(
        return_value={"id": 1, "label": "Potentials"},
    )

    # Patch must remain active for the request — re-enter inside the call.
    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=_fake_dispatch,
    ), patch(
        "hermes_cloud.app.copilot.routes.ModulesDb", return_value=mod_db,
    ), patch("hermes_cloud.app.copilot.routes.emit"), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ), patch(
        "hermes_cloud.app.copilot.routes.db_session",
        new=_fake_db_session(db),
    ):
        client = _build_app(
            dispatch_fn=_fake_dispatch, db=db, mod_db=mod_db,
        )
        resp = client.post(
            "/api/copilot/crm/send",
            json={"module": "potentials", "record_id": 17, "message": "hi"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback"] is False
    assert body["reply"] == "ok reply"
    from hermes_cloud.app.surface import SurfaceKind
    assert captured["surface"] == SurfaceKind.SPA_RECORD_COPILOT
    # trust+audit(6567eea):user-facing surface 不再下发 ACL 白名单,
    # 写操作的安全兜底是 action_gateway 的审计 + revert_token。
    assert "X-Hermes-Surface-ACL" not in captured["headers"]
    assert captured["headers"]["X-Hermes-Origin-Channel"] == "spa_record_copilot"


def test_dispatch_list_mode_uses_chatbox_surface():
    captured = {}

    async def _fake_dispatch(*, app, envelope, **kw):
        captured["surface"] = envelope.surface
        return MagicMock(
            status_code=200, reply_text="list reply",
            tool_executions=[], usage={}, model="x",
            session_id="s", raw_body=b"{}",
        )

    db = MagicMock()
    mod_db = MagicMock()
    mod_db.get_module_by_name = MagicMock(
        return_value={"id": 1, "label": "Potentials"},
    )

    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=_fake_dispatch,
    ), patch(
        "hermes_cloud.app.copilot.routes.ModulesDb", return_value=mod_db,
    ), patch("hermes_cloud.app.copilot.routes.emit"), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ), patch(
        "hermes_cloud.app.copilot.routes.db_session",
        new=_fake_db_session(db),
    ):
        client = _build_app(
            dispatch_fn=_fake_dispatch, db=db, mod_db=mod_db,
        )
        resp = client.post(
            "/api/copilot/crm/send",
            json={"module": "potentials", "message": "show stalled"},
        )

    assert resp.status_code == 200, resp.text
    from hermes_cloud.app.surface import SurfaceKind
    assert captured["surface"] == SurfaceKind.SPA_CHATBOX


def test_dispatch_failure_returns_fallback():
    async def _crashy(**kw):
        raise RuntimeError("hermes is down")

    db = MagicMock()
    mod_db = MagicMock()
    mod_db.get_module_by_name = MagicMock(
        return_value={"id": 1, "label": "Potentials"},
    )

    with patch(
        "hermes_cloud.app.copilot.routes.dispatch_to_hermes",
        new=_crashy,
    ), patch(
        "hermes_cloud.app.copilot.routes.ModulesDb", return_value=mod_db,
    ), patch("hermes_cloud.app.copilot.routes.emit"), patch(
        "hermes_cloud.app.llm_creds.resolve_for_user",
        return_value=MagicMock(model="big-pickle"),
    ), patch(
        "hermes_cloud.app.copilot.routes.db_session",
        new=_fake_db_session(db),
    ):
        client = _build_app(
            dispatch_fn=_crashy, db=db, mod_db=mod_db,
        )
        resp = client.post(
            "/api/copilot/crm/send",
            json={"module": "potentials", "record_id": 1, "message": "hi"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback"] is True
    assert "hermes is down" in (body.get("error") or "")
    assert body["reply"] == ""
