"""@mention context propagation — Charter §一定律 1.

Charter mandates events keep their full original context. Feishu
``@`` mentions are part of how a user expresses intent ("ask 张总
about this, not me"); losing them destroys the agent's ability to
reason about who was addressed.

This file pins the touchpoint adapter's mention handling:
``FeishuAdapter.normalize`` → ``interactions.content.mentions``.

The cloud-side bridge tests were removed in the per-user-channel-bot
migration (Phase 99) — feishu inbound now runs natively in each
profile's hermes subprocess, not through a cloud bridge.
"""
from __future__ import annotations

import json  # noqa: F401  (kept — touchpoint payloads use json below)


def test_touchpoint_adapter_lifts_mentions_from_payload():
    """``content.mentions`` arrives as JSON-in-string from the bridge;
    the adapter must lift it into ``interactions.content`` so RL /
    memory queries can read it without re-parsing."""
    from hermes_cloud.app.touchpoints.feishu import FeishuAdapter
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_alice"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_x",
                "chat_type": "group",
                "content": json.dumps({
                    "text": "@张总 这单怎么样了？",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "name": "张总",
                            "open_id": "ou_zhang",
                            "user_id": "",
                        },
                    ],
                }, ensure_ascii=False),
            },
        },
    }
    norm = FeishuAdapter().normalize(payload)
    assert norm.content["text"] == "@张总 这单怎么样了？"
    assert norm.content["mentions"] == [{
        "key": "@_user_1",
        "name": "张总",
        "open_id": "ou_zhang",
        "user_id": "",
    }]


def test_touchpoint_adapter_no_mentions_omits_field():
    """Messages without @s shouldn't get a vacuous ``mentions: []``."""
    from hermes_cloud.app.touchpoints.feishu import FeishuAdapter
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_alice"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_x",
                "chat_type": "p2p",
                "content": json.dumps({"text": "hello"}, ensure_ascii=False),
            },
        },
    }
    norm = FeishuAdapter().normalize(payload)
    assert "mentions" not in norm.content


def test_touchpoint_adapter_empty_mentions_list_omits_field():
    """Bridge that emits ``mentions: []`` (no @s extracted) shouldn't
    set the field on the row either — keeps interactions content lean."""
    from hermes_cloud.app.touchpoints.feishu import FeishuAdapter
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_alice"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_x",
                "chat_type": "p2p",
                "content": json.dumps({
                    "text": "hello",
                    "mentions": [],
                }, ensure_ascii=False),
            },
        },
    }
    norm = FeishuAdapter().normalize(payload)
    assert "mentions" not in norm.content
