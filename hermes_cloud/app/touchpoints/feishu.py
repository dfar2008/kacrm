"""Feishu (Lark) IM adapter.

hermes already has a feishu long-connection pipeline (see
``docs/2026-04-14-feishu-long-connection.md``). This adapter turns a
normalized Feishu ``im.message.receive_v1`` event into an interaction
row. Plug it in wherever the long connection dispatches message
events (or via the generic ``POST /api/touchpoints/{source}``).

Expected payload (trimmed Feishu shape)::

    {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_xxx", "user_id": "..."},
                       "sender_type": "user"},
            "message": {
                "message_id": "om_xxx",
                "chat_id": "oc_xxx",
                "chat_type": "p2p",
                "content": '{"text":"hi"}',      # Feishu sends JSON-in-string
                "create_time": "1712000000000"
            }
        }
    }

Either the adapter is called with ``subjects`` + ``user_id`` already
resolved (preferred — the long connection knows who bound this chat),
or it falls back to resolving via sender open_id (channel_bindings).
If no binding is found the row lands unlinked (empty ``subjects``).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from hermes_cloud.app.touchpoints._im import resolve_subjects_by_identifier
from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


def _extract_text(content_raw: Any) -> str:
    if isinstance(content_raw, dict):
        return str(content_raw.get("text") or "")
    if isinstance(content_raw, str):
        try:
            c = json.loads(content_raw)
            if isinstance(c, dict):
                return str(c.get("text") or "")
        except Exception:
            return content_raw
    return ""


def _extract_content_obj(content_raw: Any) -> Dict[str, Any]:
    """Return the parsed content dict (with text + optional mentions etc.)
    or an empty dict if the raw payload doesn't decode."""
    if isinstance(content_raw, dict):
        return content_raw
    if isinstance(content_raw, str):
        try:
            c = json.loads(content_raw)
            return c if isinstance(c, dict) else {}
        except Exception:
            return {}
    return {}


class FeishuAdapter(TouchpointAdapter):
    source = "channel:feishu"
    default_kind = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        event = payload.get("event") or payload
        msg = event.get("message") or {}
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}

        content_obj = _extract_content_obj(msg.get("content"))
        text = str(content_obj.get("text") or "")
        ts_ms = msg.get("create_time")
        occurred_at = None
        if ts_ms:
            try:
                occurred_at = datetime.fromtimestamp(
                    int(ts_ms) / 1000, tz=timezone.utc,
                )
            except Exception:
                pass

        open_id: str | None = sender_id.get("open_id") or None

        content = {
            "platform": "feishu",
            "text": text,
            "chat_id": msg.get("chat_id"),
            "chat_type": msg.get("chat_type"),
            "message_id": msg.get("message_id"),
            "open_id": open_id,
        }
        # Charter §一定律 1 — preserve @mention metadata. Pulled from
        # the bridge's SDK extract; lets the agent reason about
        # "老板 @ me" vs "I @ them" and call Feishu OpenAPI back with
        # stable open_ids when responding.
        if isinstance(content_obj, dict) and content_obj.get("mentions"):
            content["mentions"] = content_obj["mentions"]

        actors = []
        bound_user = payload.get("bound_user_id")
        if bound_user:
            # Charter §1 渠道闭环: stamp the user actor with the channel
            # source + external IDs so origin_context.from_interaction_actors
            # can route the agent's pending approvals back to feishu.
            user_actor: Dict[str, Any] = {
                "type": "user",
                "id": int(bound_user),
                "source": "feishu",
            }
            if open_id:
                user_actor["external_user_id"] = open_id
            if msg.get("message_id"):
                user_actor["external_message_id"] = msg["message_id"]
            if msg.get("chat_id"):
                user_actor["external_chat_id"] = msg["chat_id"]
            actors.append(user_actor)
        if open_id:
            actors.append({
                "type": "external",
                "platform": "feishu",
                "open_id": open_id,
            })

        # --- subjects resolution -------------------------------------------
        # Priority 1: explicit subjects from caller (long connection已绑定用户)
        subjects = list(payload.get("subjects") or [])

        # Priority 2: resolve via sender open_id → channel_bindings
        # resolve_subjects_by_identifier is best-effort; if the DB is
        # unavailable (e.g. test environment) it raises — treat as miss.
        if not subjects and open_id:
            try:
                resolved = resolve_subjects_by_identifier(
                    external_id=open_id,
                    channel="feishu",
                    db=payload.get("_db"),  # caller may inject db via payload
                )
            except Exception:
                resolved = []
            if resolved:
                subjects = resolved
            # 未命中 → 仍记录为未挂载互动(subjects 为空)。原「pending_claim」
            # 认领队列标记已随认领功能一并移除。

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source=self.source,
            subjects=subjects,
            actors=actors,
            occurred_at=occurred_at,
            provenance={"channel": "feishu"},
        )


register_adapter(FeishuAdapter())

__all__ = ["FeishuAdapter"]
