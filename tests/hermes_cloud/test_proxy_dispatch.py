"""Tests for ``app.proxy.dispatch.dispatch_to_hermes``.

Mocks app.state.* so tests are pure unit — no real hermes subprocess,
no real http_client, no real DB. Asserts the wire-format we send to
hermes (URL, headers, body) and that surface envelope plumbing
(headers, session_id) survives end-to-end.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes_cloud.app.proxy import dispatch_to_hermes
from hermes_cloud.app.surface import (
    HermesRequestEnvelope, SurfaceKind, inject_context,
)
import hermes_cloud.app.surface.session as session_mod


# ──────────────────────────────────────────────────────────────────
# Test app.state mocks
# ──────────────────────────────────────────────────────────────────


def _make_fake_response(
    status: int = 200, body: Dict[str, Any] | None = None,
) -> MagicMock:
    body = body or {
        "choices": [{
            "message": {"role": "assistant", "content": "ok"},
        }],
        "model": "big-pickle",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    raw = json.dumps(body).encode("utf-8")
    resp = MagicMock()
    resp.status_code = status
    resp.content = raw
    resp.headers = {"content-type": "application/json"}
    return resp


def _make_fake_app(
    response: MagicMock | None = None,
    profile_id: str = "default-profile",
    port: int = 9999,
) -> MagicMock:
    """Build a MagicMock that quacks enough like a FastAPI app for
    dispatch_to_hermes. AsyncMock-wrapped methods where needed."""
    response = response or _make_fake_response()

    profile = MagicMock(id=profile_id)
    profile_store = MagicMock()
    profile_store.ensure_default_profile.return_value = profile

    profile_manager = MagicMock()
    profile_manager.ensure_running = AsyncMock(return_value=port)
    profile_manager.touch = MagicMock()

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)

    app = MagicMock()
    app.state.profile_store = profile_store
    app.state.profile_manager = profile_manager
    app.state.http_client = http_client
    return app


def _envelope_for(surface=SurfaceKind.FEISHU, user_id: int = 7, text: str = "hi"):
    """Build a real envelope (not mocked) so we exercise the surface
    layer's header / session synthesis end-to-end."""
    saved = session_mod.lookup_user_tz
    session_mod.lookup_user_tz = lambda uid: "UTC"
    try:
        return inject_context(text=text, user_id=user_id, surface=surface)
    finally:
        session_mod.lookup_user_tz = saved


# ──────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_returns_reply_text_and_usage():
    app = _make_fake_app()
    envelope = _envelope_for(surface=SurfaceKind.FEISHU, user_id=7, text="hi")

    result = await dispatch_to_hermes(app=app, envelope=envelope)

    assert result.status_code == 200
    assert result.reply_text == "ok"
    assert result.model == "big-pickle"
    assert result.usage["prompt_tokens"] == 10
    assert result.session_id == envelope.session_id


@pytest.mark.asyncio
async def test_dispatch_forwards_to_correct_local_url():
    app = _make_fake_app(port=12345)
    envelope = _envelope_for(user_id=7)

    await dispatch_to_hermes(app=app, envelope=envelope)

    call = app.state.http_client.post.call_args
    posted_url = call.args[0]
    assert posted_url == "http://127.0.0.1:12345/v1/chat/completions"


@pytest.mark.asyncio
async def test_dispatch_propagates_envelope_headers_to_hermes():
    """Origin channel (patch 20) + session id headers must reach
    hermes. Surface ACL (patch 27) is trust+audit since 6567eea:
    user-facing surfaces (incl. feishu) carry NO allowlist header —
    safety comes from action_gateway audit + revert_token."""
    app = _make_fake_app()
    envelope = _envelope_for(surface=SurfaceKind.FEISHU, user_id=42)

    await dispatch_to_hermes(app=app, envelope=envelope, bearer_key="secret")

    call = app.state.http_client.post.call_args
    headers = call.kwargs["headers"]
    # Surface ACL (patch 27) — no allowlist for user-facing surfaces
    assert "X-Hermes-Surface-ACL" not in headers
    # Origin channel (patch 20)
    assert headers["X-Hermes-Origin-Channel"] == "feishu"
    # Session id (Phase 4 §4.7)
    assert headers["X-Hermes-Session-Id"] == envelope.session_id
    # Auth bearer
    assert headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_dispatch_chatbox_omits_acl_header():
    """SPA chatbox surface_acl_for() returns None → no ACL header,
    patch 27 falls back to back-compat allow-all."""
    app = _make_fake_app()
    envelope = _envelope_for(surface=SurfaceKind.SPA_CHATBOX, user_id=7)

    await dispatch_to_hermes(app=app, envelope=envelope)

    headers = app.state.http_client.post.call_args.kwargs["headers"]
    assert "X-Hermes-Surface-ACL" not in headers
    assert headers["X-Hermes-Origin-Channel"] == "spa_chatbox"


@pytest.mark.asyncio
async def test_dispatch_extracts_tool_calls_from_response():
    response = _make_fake_response(body={
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [{
                    "id": "call_0",
                    "type": "function",
                    "function": {
                        "name": "list_related_records",
                        "arguments": json.dumps({"target_module": "accounts"}),
                    },
                }],
            },
        }],
        "model": "big-pickle",
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    })
    app = _make_fake_app(response=response)
    envelope = _envelope_for()

    result = await dispatch_to_hermes(app=app, envelope=envelope)

    assert len(result.tool_executions) == 1
    te = result.tool_executions[0]
    assert te["name"] == "list_related_records"
    assert te["args"]["target_module"] == "accounts"


@pytest.mark.asyncio
async def test_dispatch_body_extras_merged_into_request():
    app = _make_fake_app()
    envelope = _envelope_for(text="hi")

    await dispatch_to_hermes(
        app=app, envelope=envelope,
        body_extras={"temperature": 0.7, "model": "big-pickle"},
    )

    call = app.state.http_client.post.call_args
    body = json.loads(call.kwargs["content"])
    assert body["temperature"] == 0.7
    assert body["model"] == "big-pickle"
    # messages always come from envelope, not body_extras
    assert body["messages"] == envelope.messages
    # stream is forced False — this helper is non-streaming
    assert body["stream"] is False


@pytest.mark.asyncio
async def test_dispatch_messages_from_envelope_not_overridable():
    """Caller can't smuggle messages via body_extras — the envelope's
    messages list is authoritative so surface-layer context injection
    isn't accidentally bypassed."""
    app = _make_fake_app()
    envelope = _envelope_for(text="real message")

    await dispatch_to_hermes(
        app=app, envelope=envelope,
        body_extras={"messages": [{"role": "user", "content": "smuggled"}]},
    )

    body = json.loads(app.state.http_client.post.call_args.kwargs["content"])
    assert body["messages"] == envelope.messages
    assert body["messages"] != [{"role": "user", "content": "smuggled"}]


# ──────────────────────────────────────────────────────────────────
# Error paths
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_transport_error_returns_503():
    app = _make_fake_app()
    app.state.http_client.post = AsyncMock(side_effect=Exception("boom"))
    envelope = _envelope_for()

    result = await dispatch_to_hermes(app=app, envelope=envelope)

    assert result.status_code == 503
    assert b"process_unavailable" in result.raw_body


@pytest.mark.asyncio
async def test_dispatch_4xx_response_propagated():
    response = _make_fake_response(status=400, body={"error": "bad request"})
    app = _make_fake_app(response=response)
    envelope = _envelope_for()

    result = await dispatch_to_hermes(app=app, envelope=envelope)

    assert result.status_code == 400
    assert result.reply_text == ""  # no parsing on 4xx
    assert b"bad request" in result.raw_body


@pytest.mark.asyncio
async def test_dispatch_malformed_json_response_doesnt_crash():
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"not-json-at-all"
    resp.headers = {"content-type": "application/json"}
    app = _make_fake_app(response=resp)
    envelope = _envelope_for()

    result = await dispatch_to_hermes(app=app, envelope=envelope)

    assert result.status_code == 200
    assert result.reply_text == ""
    assert result.raw_body == b"not-json-at-all"


# ──────────────────────────────────────────────────────────────────
# Profile resolution
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_uses_default_profile_when_none_supplied():
    app = _make_fake_app(profile_id="profile-default-7")
    envelope = _envelope_for(user_id=7)

    await dispatch_to_hermes(app=app, envelope=envelope)

    app.state.profile_store.ensure_default_profile.assert_called_once_with(user_id="7")
    app.state.profile_manager.ensure_running.assert_called_once()
    assert app.state.profile_manager.ensure_running.call_args.args[0] == "profile-default-7"


@pytest.mark.asyncio
async def test_dispatch_explicit_profile_id_skips_default_lookup():
    app = _make_fake_app()
    envelope = _envelope_for()

    await dispatch_to_hermes(
        app=app, envelope=envelope, profile_id="explicit-profile",
    )

    app.state.profile_store.ensure_default_profile.assert_not_called()
    assert app.state.profile_manager.ensure_running.call_args.args[0] == "explicit-profile"


# ──────────────────────────────────────────────────────────────────
# Usage capture
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_records_usage_with_surface_in_purpose(monkeypatch):
    captured = []

    def _fake_record(*, user_id, purpose, msg, session_id=None):
        captured.append({
            "user_id": user_id, "purpose": purpose,
            "model": msg.get("__model"), "session_id": session_id,
        })

    monkeypatch.setattr(
        "hermes_cloud.app.insights.llm_usage.record_llm_usage", _fake_record,
    )
    app = _make_fake_app()
    envelope = _envelope_for(surface=SurfaceKind.FEISHU, user_id=7)

    await dispatch_to_hermes(app=app, envelope=envelope)

    assert len(captured) == 1
    rec = captured[0]
    assert rec["user_id"] == 7
    assert rec["purpose"] == "surface:feishu"
    assert rec["model"] == "big-pickle"
    assert rec["session_id"] == envelope.session_id


@pytest.mark.asyncio
async def test_dispatch_record_usage_can_be_disabled(monkeypatch):
    """When the caller doesn't want telemetry (e.g. dry-run smoke test),
    record_usage=False suppresses the write."""
    captured = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.llm_usage.record_llm_usage",
        lambda **kw: captured.append(kw),
    )
    app = _make_fake_app()
    envelope = _envelope_for()

    await dispatch_to_hermes(app=app, envelope=envelope, record_usage=False)

    assert captured == []
