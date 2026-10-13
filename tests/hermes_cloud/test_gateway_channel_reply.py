"""``channel_reply.reply_via_origin`` — Charter §1 回弹 confirmation.

When a user types "通过 abc12345" on a non-WebUI channel, the celery
fast-path approves the action AND should echo a confirmation back to
the same chat so the user knows it worked. Without this回弹, the chat
looks like the bot ignored them.

These tests pin the channel→platform mapping and the missing-field
graceful-skip behaviour.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest


@pytest.mark.parametrize("channel,expected_platform", [
    ("feishu",      "feishu"),
    ("wecom_ilink", "weixin"),
    ("discord",     "discord"),
    ("telegram",    "telegram"),
    ("slack",       "slack"),
    ("dingtalk",    "dingtalk"),
])
def test_reply_via_origin_maps_channel_to_platform(monkeypatch, channel, expected_platform):
    from hermes.action_gateway import channel_reply

    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: captured.append(kw) or True,
    )

    ok = channel_reply.reply_via_origin(
        origin={"channel": channel, "external_user_id": "u_test"},
        profile_id="42",
        text="✅ 已通过审批 abc12345",
    )
    assert ok is True
    assert len(captured) == 1
    assert captured[0]["platform"] == expected_platform
    assert captured[0]["channel_id"] == "u_test"
    assert captured[0]["text"] == "✅ 已通过审批 abc12345"
    assert captured[0]["profile_id"] == "42"


def test_reply_via_origin_falls_back_to_chat_id(monkeypatch):
    from hermes.action_gateway import channel_reply
    captured: List[Any] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: captured.append(kw) or True,
    )

    channel_reply.reply_via_origin(
        origin={"channel": "discord", "external_chat_id": "room_42"},
        profile_id="42",
        text="hello",
    )
    assert len(captured) == 1
    assert captured[0]["channel_id"] == "room_42"


def test_reply_via_origin_unknown_channel_skips(monkeypatch, caplog):
    import logging
    from hermes.action_gateway import channel_reply

    posted: List[Any] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: posted.append(kw) or True,
    )
    caplog.set_level(logging.WARNING, logger="hermes.action_gateway.channel_reply")

    ok = channel_reply.reply_via_origin(
        origin={"channel": "_no_such_channel"},
        profile_id="42",
        text="x",
    )
    assert ok is False
    assert posted == []


def test_reply_via_origin_no_target_skips(monkeypatch, caplog):
    import logging
    from hermes.action_gateway import channel_reply

    posted: List[Any] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: posted.append(kw) or True,
    )
    caplog.set_level(logging.WARNING, logger="hermes.action_gateway.channel_reply")

    ok = channel_reply.reply_via_origin(
        origin={"channel": "wecom_ilink"},  # no user/chat
        profile_id="42",
        text="x",
    )
    assert ok is False
    assert posted == []


def test_reply_via_origin_post_failure_returns_false(monkeypatch):
    from hermes.action_gateway import channel_reply

    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: False,  # upstream returned ok=False
    )
    ok = channel_reply.reply_via_origin(
        origin={"channel": "wecom_ilink", "external_user_id": "wxid_x"},
        profile_id="42",
        text="x",
    )
    assert ok is False


def test_reply_via_origin_post_exception_swallowed(monkeypatch):
    """A flaky channel adapter must not crash the celery task that
    already successfully approved/rejected the action."""
    from hermes.action_gateway import channel_reply

    def _boom(**kw):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud", _boom,
    )
    # Must not raise.
    ok = channel_reply.reply_via_origin(
        origin={"channel": "wecom_ilink", "external_user_id": "wxid_x"},
        profile_id="42",
        text="x",
    )
    assert ok is False
