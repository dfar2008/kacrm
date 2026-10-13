"""Slack Events API adapter.

Expected payload shape: the ``event`` subfield of Slack's Events API
``event_callback`` envelope (``type=message``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class SlackAdapter(TouchpointAdapter):
    source = "channel:slack"
    default_kind = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        event = payload.get("event") or payload
        ts = event.get("ts") or event.get("event_ts")
        occurred_at = None
        if ts:
            try:
                occurred_at = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except Exception:
                pass

        content = {
            "platform": "slack",
            "text": str(event.get("text") or ""),
            "channel": event.get("channel"),
            "channel_type": event.get("channel_type"),
            "thread_ts": event.get("thread_ts"),
            "event_ts": ts,
            "user": event.get("user"),
            "team": payload.get("team_id") or event.get("team"),
        }

        actors = []
        if payload.get("bound_user_id"):
            # Charter §1 渠道闭环.
            user_actor: Dict[str, Any] = {
                "type": "user",
                "id": int(payload["bound_user_id"]),
                "source": "slack",
            }
            if event.get("user"):
                user_actor["external_user_id"] = str(event["user"])
            if ts:
                user_actor["external_message_id"] = str(ts)
            if event.get("channel"):
                user_actor["external_chat_id"] = str(event["channel"])
            actors.append(user_actor)
        if event.get("user"):
            actors.append({
                "type": "external",
                "platform": "slack",
                "user_id": event["user"],
                "team": content["team"],
            })

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source=self.source,
            subjects=list(payload.get("subjects") or []),
            actors=actors,
            occurred_at=occurred_at,
            provenance={"channel": "slack"},
        )


register_adapter(SlackAdapter())

__all__ = ["SlackAdapter"]
