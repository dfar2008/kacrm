"""WeCom (企业微信) IM adapter.

WeCom push callback (XML or JSON, depending on how the enterprise
app is configured). We accept the JSON-normalized shape after
signature verification — the signature step belongs in whichever
FastAPI handler proxies the callback.

Shape::

    {
        "MsgType": "text",
        "Content": "hi",
        "FromUserName": "wm_ext_xxxxx",   # external contact userid
        "ToUserName": "self_userid",
        "OpenKfId":   "...",              # 客服场景
        "CreateTime": 1712000000,
        "MsgId":      "...",
        "bound_user_id": 7,               # resolved by caller
        "subjects":   [...]
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from hermes_cloud.app.touchpoints._im import resolve_subjects_by_identifier
from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class WeComAdapter(TouchpointAdapter):
    source = "channel:wecom"
    default_kind = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        ts = payload.get("CreateTime") or payload.get("create_time")
        occurred_at = None
        if ts:
            try:
                occurred_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                pass

        msg_type = str(payload.get("MsgType") or payload.get("msgtype") or "text")
        text = payload.get("Content") or payload.get("content") or ""

        content = {
            "platform": "wecom",
            "msg_type": msg_type,
            "text": str(text),
            "from_user": payload.get("FromUserName") or payload.get("from_user"),
            "to_user":   payload.get("ToUserName")   or payload.get("to_user"),
            "msg_id":    payload.get("MsgId")        or payload.get("msg_id"),
            "open_kf_id": payload.get("OpenKfId"),
        }

        actors = []
        if payload.get("bound_user_id"):
            # Charter §1 渠道闭环: stamp the user actor so the
            # origin_context can route gateway approvals back to wecom.
            user_actor: Dict[str, Any] = {
                "type": "user",
                "id": int(payload["bound_user_id"]),
                "source": "wecom",
            }
            if content.get("from_user"):
                user_actor["external_user_id"] = content["from_user"]
            if content.get("msg_id"):
                user_actor["external_message_id"] = str(content["msg_id"])
            if content.get("open_kf_id"):
                user_actor["external_chat_id"] = str(content["open_kf_id"])
            actors.append(user_actor)
        if content.get("from_user"):
            actors.append({
                "type": "external",
                "platform": "wecom",
                "user_id": content["from_user"],
            })

        # --- subjects resolution -------------------------------------------
        subjects = list(payload.get("subjects") or [])
        from_user: str | None = content.get("from_user") or None

        if not subjects and from_user:
            try:
                resolved = resolve_subjects_by_identifier(
                    external_id=from_user,
                    channel="wecom",
                    db=payload.get("_db"),
                )
            except Exception:
                resolved = []
            if resolved:
                subjects = resolved
            # Unresolved senders are recorded as unattached interactions
            # (empty subjects). The former "pending_claim" claim-queue
            # marker was removed along with the claim feature.

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source=self.source,
            subjects=subjects,
            actors=actors,
            occurred_at=occurred_at,
            provenance={"channel": "wecom"},
        )


register_adapter(WeComAdapter())

__all__ = ["WeComAdapter"]
