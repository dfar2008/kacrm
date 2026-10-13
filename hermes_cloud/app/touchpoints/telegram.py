"""Telegram Bot API adapter.

Expects the ``message`` subfield of a Telegram ``Update`` payload.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class TelegramAdapter(TouchpointAdapter):
    source = "channel:telegram"
    default_kind = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        msg = payload.get("message") or payload.get("edited_message") or payload
        ts = msg.get("date")
        occurred_at = None
        if ts:
            try:
                occurred_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                pass

        chat = msg.get("chat") or {}
        from_user = msg.get("from") or {}

        content = {
            "platform": "telegram",
            "text": str(msg.get("text") or msg.get("caption") or ""),
            "chat_id": chat.get("id"),
            "chat_type": chat.get("type"),
            "message_id": msg.get("message_id"),
            "from_id": from_user.get("id"),
            "from_username": from_user.get("username"),
        }

        actors = []
        if payload.get("bound_user_id"):
            # Charter §1 渠道闭环.
            user_actor: Dict[str, Any] = {
                "type": "user",
                "id": int(payload["bound_user_id"]),
                "source": "telegram",
            }
            if from_user.get("id"):
                user_actor["external_user_id"] = str(from_user["id"])
            if msg.get("message_id"):
                user_actor["external_message_id"] = str(msg["message_id"])
            if chat.get("id"):
                user_actor["external_chat_id"] = str(chat["id"])
            actors.append(user_actor)
        if from_user.get("id"):
            actors.append({
                "type": "external",
                "platform": "telegram",
                "user_id": from_user["id"],
                "username": from_user.get("username"),
            })

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source=self.source,
            subjects=list(payload.get("subjects") or []),
            actors=actors,
            occurred_at=occurred_at,
            provenance={"channel": "telegram"},
        )


register_adapter(TelegramAdapter())

__all__ = ["TelegramAdapter"]
