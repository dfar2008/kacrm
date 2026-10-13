"""Pending-approval notifiers for discord / telegram / slack / dingtalk.

All four channels share the text-based notifier produced by
:func:`hermes_cloud.app.gateway.text_notifier.make_notifier`. The
prompt body + reply-command parser path is identical to wecom_ilink;
this file just pins each channel module wires them up at import time.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest


CHANNELS = [
    ("discord",  "discord"),
    ("telegram", "telegram"),
    ("slack",    "slack"),
    ("dingtalk", "dingtalk"),
]


@pytest.mark.parametrize("channel,platform", CHANNELS)
def test_notifier_registered_at_import(channel, platform):
    from hermes_cloud.app.channels import discord, telegram, slack, dingtalk  # noqa: F401
    from hermes.action_gateway import notifiers
    assert notifiers.get(channel) is not None, (
        f"{channel} channel module did not register a pending notifier"
    )


@pytest.mark.parametrize("channel,platform", CHANNELS)
def test_notifier_posts_text_via_cloud(monkeypatch, channel, platform):
    from hermes_cloud.app.channels import discord, telegram, slack, dingtalk  # noqa: F401
    from hermes.action_gateway import notifiers

    captured: List[Dict[str, Any]] = []

    def _fake_post(*, user_id, profile_id, platform, channel_id, text):
        captured.append({
            "user_id": user_id, "profile_id": profile_id,
            "platform": platform, "channel_id": channel_id, "text": text,
        })
        return True

    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud", _fake_post,
    )

    notifiers.notify_pending(
        {"id": f"abcd1234-{channel[:4]}-0000-0000-000000000000",
         "kind": "crm.write", "agent_name": "intake",
         "summary": "create lead", "profile_id": "42"},
        {"channel": channel, "external_user_id": f"{channel}_user_x"},
    )
    assert len(captured) == 1
    c = captured[0]
    assert c["channel_id"] == f"{channel}_user_x"
    assert c["profile_id"] == "42"
    assert c["platform"] == platform
    assert "abcd1234" in c["text"]
    assert "通过 abcd1234" in c["text"]


@pytest.mark.parametrize("channel,platform", CHANNELS)
def test_notifier_falls_back_to_chat_id_when_user_missing(monkeypatch, channel, platform):
    """Group-chat origins (no DM user, but a chat_id) still get the prompt
    posted into the originating chat — better than dropping it."""
    from hermes_cloud.app.channels import discord, telegram, slack, dingtalk  # noqa: F401
    from hermes.action_gateway import notifiers

    captured: List[Any] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: captured.append(kw) or True,
    )

    notifiers.notify_pending(
        {"id": "x", "kind": "crm.write", "profile_id": "42"},
        {"channel": channel, "external_chat_id": "chat_room_42"},
    )
    assert len(captured) == 1
    assert captured[0]["channel_id"] == "chat_room_42"


@pytest.mark.parametrize("channel,_p", CHANNELS)
def test_notifier_skips_when_no_target(monkeypatch, channel, _p):
    from hermes_cloud.app.channels import discord, telegram, slack, dingtalk  # noqa: F401
    from hermes.action_gateway import notifiers

    posted: List[Any] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: posted.append(kw) or True,
    )

    notifiers.notify_pending(
        {"id": "x", "profile_id": "42"},
        {"channel": channel},  # no external_user_id, no external_chat_id
    )
    assert posted == []


def test_build_approval_text_format():
    from hermes.action_gateway.text_notifier import build_approval_text
    text = build_approval_text({
        "id": "deadbeef-aaaa-bbbb-cccc-000000000001",
        "kind": "crm.write",
        "agent_name": "intake",
        "summary": "create lead Acme",
    })
    assert "审批" in text
    assert "crm.write" in text
    assert "intake" in text
    assert "Acme" in text
    assert "通过 deadbeef" in text
    assert "拒绝 deadbeef" in text


def test_build_approval_text_handles_empty_summary():
    from hermes.action_gateway.text_notifier import build_approval_text
    text = build_approval_text({
        "id": "abcd1234-1111-2222-3333-444444444444",
        "kind": "crm.write",
        "agent_name": "intake",
    })
    assert "无摘要" in text
