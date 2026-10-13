"""WeChat (个人微信) IM adapter.

Personal WeChat has no official API. In practice the payload arrives
from a 3rd-party protocol layer (itchat / WeChatFerry / 某商业协议网关).
This adapter treats the payload shape as (roughly) the WeChatFerry
``WxMsg`` schema, but is lenient about field names so different
protocol layers can all feed us.

Shape::

    {
        "from_wxid": "wxid_xxx",
        "to_wxid":   "wxid_self",
        "content":   "hi",
        "msg_type":  1,              # 1=text, 3=image, 34=voice, ...
        "room_id":   "xxx@chatroom", # if group
        "ts":        1712000000,
        "bound_user_id": 7,
        "subjects":  [...]
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from hermes_cloud.app.touchpoints._im import resolve_subjects_by_identifier
from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


_WX_MSG_TYPE_NAMES = {
    1: "text",
    3: "image",
    34: "voice",
    43: "video",
    47: "sticker",
    48: "location",
    49: "link_or_file",
}


class WeChatAdapter(TouchpointAdapter):
    source = "channel:wechat"
    default_kind = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        ts = payload.get("ts") or payload.get("CreateTime")
        occurred_at = None
        if ts:
            try:
                occurred_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                pass

        msg_type_code = payload.get("msg_type") or payload.get("MsgType") or 1
        try:
            msg_type_name = _WX_MSG_TYPE_NAMES.get(int(msg_type_code), "unknown")
        except Exception:
            msg_type_name = str(msg_type_code)

        content = {
            "platform": "wechat",
            "msg_type": msg_type_name,
            "msg_type_code": msg_type_code,
            "text": str(payload.get("content") or ""),
            "from_wxid": payload.get("from_wxid"),
            "to_wxid": payload.get("to_wxid"),
            "room_id": payload.get("room_id"),
            "is_group": bool(payload.get("room_id")),
        }

        actors = []
        bound_user_id = payload.get("bound_user_id")
        if bound_user_id:
            # Charter §1 渠道闭环: stamp the user actor with channel +
            # external IDs so the origin_context can route gateway
            # approvals back to the wecom_ilink channel.
            user_actor: Dict[str, Any] = {
                "type": "user",
                "id": int(bound_user_id),
                "source": "wechat",
            }
            if content["from_wxid"]:
                user_actor["external_user_id"] = content["from_wxid"]
            if payload.get("message_id"):
                user_actor["external_message_id"] = str(payload["message_id"])
            if content.get("room_id"):
                user_actor["external_chat_id"] = str(content["room_id"])
            actors.append(user_actor)
        if content["from_wxid"]:
            actors.append({
                "type": "external",
                "platform": "wechat",
                "wxid": content["from_wxid"],
            })

        # --- subjects resolution -------------------------------------------
        subjects = list(payload.get("subjects") or [])
        from_wxid: str | None = content.get("from_wxid") or None

        if not subjects and from_wxid:
            try:
                resolved = resolve_subjects_by_identifier(
                    external_id=from_wxid,
                    channel="wechat",
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
            provenance={"channel": "wechat"},
        )


register_adapter(WeChatAdapter())

__all__ = ["WeChatAdapter"]
