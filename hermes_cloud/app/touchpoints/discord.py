"""Discord adapter (Interactions API or gateway MESSAGE_CREATE shape)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class DiscordAdapter(TouchpointAdapter):
    source = "channel:discord"
    default_kind = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        ts_raw = payload.get("timestamp")
        occurred_at = None
        if ts_raw:
            try:
                occurred_at = datetime.fromisoformat(
                    str(ts_raw).replace("Z", "+00:00")
                )
            except Exception:
                pass

        author = payload.get("author") or {}
        content = {
            "platform": "discord",
            "text": str(payload.get("content") or ""),
            "channel_id": payload.get("channel_id"),
            "guild_id": payload.get("guild_id"),
            "message_id": payload.get("id"),
            "author_id": author.get("id"),
            "author_name": author.get("username"),
        }

        actors = []
        if payload.get("bound_user_id"):
            # Charter §1 渠道闭环: stamp source + external IDs so
            # origin_context routes gateway approvals back to discord.
            user_actor: Dict[str, Any] = {
                "type": "user",
                "id": int(payload["bound_user_id"]),
                "source": "discord",
            }
            if author.get("id"):
                user_actor["external_user_id"] = str(author["id"])
            if payload.get("id"):
                user_actor["external_message_id"] = str(payload["id"])
            if payload.get("channel_id"):
                user_actor["external_chat_id"] = str(payload["channel_id"])
            actors.append(user_actor)
        if author.get("id"):
            actors.append({
                "type": "external",
                "platform": "discord",
                "user_id": author["id"],
                "username": author.get("username"),
            })

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source=self.source,
            subjects=list(payload.get("subjects") or []),
            actors=actors,
            occurred_at=occurred_at,
            provenance={"channel": "discord"},
        )


register_adapter(DiscordAdapter())

__all__ = ["DiscordAdapter"]
