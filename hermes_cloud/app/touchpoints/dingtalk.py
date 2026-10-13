"""DingTalk (钉钉) IM adapter.

DingTalk robot / stream-mode payload. Official shape per
https://open.dingtalk.com/document/orgapp/receive-message.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class DingTalkAdapter(TouchpointAdapter):
    source = "channel:dingtalk"
    default_kind = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        ts_ms = payload.get("createAt") or payload.get("create_at")
        occurred_at = None
        if ts_ms:
            try:
                occurred_at = datetime.fromtimestamp(
                    int(ts_ms) / 1000, tz=timezone.utc,
                )
            except Exception:
                pass

        text_node = payload.get("text") or {}
        text = (
            text_node.get("content") if isinstance(text_node, dict)
            else str(text_node or "")
        )

        content = {
            "platform": "dingtalk",
            "text": str(text or ""),
            "msg_type": payload.get("msgtype") or "text",
            "conversation_id": payload.get("conversationId"),
            "conversation_type": payload.get("conversationType"),
            "sender_id": payload.get("senderStaffId") or payload.get("senderId"),
            "sender_nick": payload.get("senderNick"),
        }

        actors = []
        if payload.get("bound_user_id"):
            # Charter §1 渠道闭环.
            user_actor: Dict[str, Any] = {
                "type": "user",
                "id": int(payload["bound_user_id"]),
                "source": "dingtalk",
            }
            if content.get("sender_id"):
                user_actor["external_user_id"] = str(content["sender_id"])
            if payload.get("msgId"):
                user_actor["external_message_id"] = str(payload["msgId"])
            if content.get("conversation_id"):
                user_actor["external_chat_id"] = str(content["conversation_id"])
            actors.append(user_actor)
        if content.get("sender_id"):
            actors.append({
                "type": "external",
                "platform": "dingtalk",
                "user_id": content["sender_id"],
                "nick": content.get("sender_nick"),
            })

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source=self.source,
            subjects=list(payload.get("subjects") or []),
            actors=actors,
            occurred_at=occurred_at,
            provenance={"channel": "dingtalk"},
        )


register_adapter(DingTalkAdapter())

__all__ = ["DingTalkAdapter"]
