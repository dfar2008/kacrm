"""Tests for ``app.surface.ilink_text`` + ``app.touchpoints.ilink_tap``
(Phase 4 v2.2 §Week 5 Day 1-3)."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_cloud.app.surface.ilink_text import (
    render_ilink_alert, render_ilink_daily_completed, render_ilink_reply,
)
from hermes_cloud.app.touchpoints.ilink_tap import dispatch_ilink_inbound


# ──────────────────────────────────────────────────────────────────
# Renderers — pure
# ──────────────────────────────────────────────────────────────────


def test_render_reply_no_pending_actions():
    out = render_ilink_reply(text="hello", pending_actions=[])
    assert out["kind"] == "ilink_text"
    assert out["text"] == "hello"
    assert out["chips"] == []


def test_render_reply_with_chips_appends_numbered_lines():
    out = render_ilink_reply(
        text="here it is",
        pending_actions=[
            {"label": "通过", "kind": "approve", "ref": "act-1"},
            {"label": "拒绝", "kind": "cancel", "ref": "act-1"},
        ],
    )
    text = out["text"]
    assert "here it is" in text
    assert "1. 通过" in text
    assert "2. 拒绝" in text
    chips = out["chips"]
    assert len(chips) == 2
    assert chips[0]["slot"] == 1
    assert chips[0]["kind"] == "approve"
    assert chips[1]["slot"] == 2


def test_render_reply_drops_non_dict_chip():
    out = render_ilink_reply(
        text="x",
        pending_actions=["not-a-dict", {"label": "approve",
                                          "kind": "approve", "ref": "r"}],
    )
    # Only the well-formed dict survives.
    assert len(out["chips"]) == 1


def test_render_daily_completed_text_only():
    out = render_ilink_daily_completed(
        user_label="Alice",
        items=[{"title": "重启 Acme", "ref": "r1"},
               {"title": "标记 Beta", "ref": "r2"}],
    )
    assert out["kind"] == "ilink_text"
    assert "Alice" in out["text"]
    assert "重启 Acme" in out["text"]
    assert "标记 Beta" in out["text"]
    assert len(out["chips"]) == 2
    # Each chip kind is 'cancel' (the most useful action on small IM screens).
    assert all(c["kind"] == "cancel" for c in out["chips"])


def test_render_alert_with_mute_chip():
    out = render_ilink_alert(
        signal_text="3 deals overdue",
        mute_signal_id="sig:overdue_payment",
    )
    assert "3 deals overdue" in out["text"]
    assert "不再提醒" in out["text"]
    assert out["chips"][0]["kind"] == "mute_signal"
    assert out["chips"][0]["ref"] == "sig:overdue_payment"


def test_render_alert_without_mute_no_chips():
    out = render_ilink_alert(signal_text="x")
    assert out["chips"] == []


# ──────────────────────────────────────────────────────────────────
# dispatch_ilink_inbound
# ──────────────────────────────────────────────────────────────────


def _result(*, status=200, reply="hi from hermes"):
    return MagicMock(
        status_code=status, reply_text=reply, raw_body=b"{}",
        session_id="user-7-day-2026-05-09",
        usage={"prompt_tokens": 5, "completion_tokens": 10},
        model="big-pickle", tool_executions=[],
    )


@pytest.mark.asyncio
async def test_dispatch_uses_ilink_surface():
    captured = {}

    async def _fake_dispatch(*, app, envelope, **kw):
        captured["surface"] = envelope.surface
        captured["headers"] = dict(envelope.headers)
        return _result()

    pm = MagicMock()
    pm.ensure_running = AsyncMock(return_value=12345)
    pm.touch = MagicMock()

    with patch(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        new=_fake_dispatch,
    ):
        out = await dispatch_ilink_inbound(
            text="hello",
            external_user_id="wxid_xxx",
            user_id=7, profile_id="prof",
            profile_manager=pm, http_client=MagicMock(),
            send_text=None,
        )

    from hermes_cloud.app.surface import SurfaceKind
    assert captured["surface"] == SurfaceKind.ILINK
    assert captured["headers"]["X-Hermes-Origin-Channel"] == "wecom_ilink"
    assert captured["headers"]["X-Hermes-Origin-External-User-Id"] == "wxid_xxx"
    # trust+audit(6567eea):ILINK 白名单已撤,ACL 头不再下发。
    assert "X-Hermes-Surface-ACL" not in captured["headers"]
    # Returned envelope is the rendered ilink_text with the body.
    assert out["kind"] == "ilink_text"
    assert "hi from hermes" in out["text"]


@pytest.mark.asyncio
async def test_dispatch_pushes_via_send_text_when_provided():
    pushes = []

    async def _send_text(uid, text):
        pushes.append((uid, text))

    pm = MagicMock()
    pm.ensure_running = AsyncMock(return_value=12345)
    pm.touch = MagicMock()

    async def _fake_dispatch(**kw):
        return _result(reply="ok body")

    with patch(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        new=_fake_dispatch,
    ):
        await dispatch_ilink_inbound(
            text="hi",
            external_user_id="wxid_xxx",
            user_id=7, profile_id="prof",
            profile_manager=pm, http_client=MagicMock(),
            send_text=_send_text,
        )

    assert len(pushes) == 1
    assert pushes[0][0] == "wxid_xxx"
    assert "ok body" in pushes[0][1]


@pytest.mark.asyncio
async def test_dispatch_empty_text_short_circuits():
    pm = MagicMock()
    pm.ensure_running = AsyncMock()
    out = await dispatch_ilink_inbound(
        text="   ",
        external_user_id="x",
        user_id=7, profile_id="p",
        profile_manager=pm, http_client=MagicMock(),
        send_text=None,
    )
    assert out["text"] == ""
    pm.ensure_running.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_swallows_exception_and_pushes_friendly_message():
    pushes = []

    async def _send_text(uid, text):
        pushes.append((uid, text))

    async def _crashy(**kw):
        raise RuntimeError("hermes is down")

    pm = MagicMock()
    pm.ensure_running = AsyncMock(return_value=12345)
    pm.touch = MagicMock()

    with patch(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        new=_crashy,
    ):
        # MUST NOT raise.
        out = await dispatch_ilink_inbound(
            text="hi",
            external_user_id="wxid_xxx",
            user_id=7, profile_id="p",
            profile_manager=pm, http_client=MagicMock(),
            send_text=_send_text,
        )

    assert "Agent 暂时不可用" in out["text"]
    assert len(pushes) == 1
    assert "hermes is down" in pushes[0][1]
