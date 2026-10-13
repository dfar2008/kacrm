"""Manual touchpoints — chat-box / add-note form / in-app compose.

Any interaction the user types into the product directly lands here.
The chat-box (charter 反转 2: "主界面 = 语言") and the "add contact
note" form both target this adapter.

Expected payload shape (``POST /api/touchpoints/note``)::

    {
        "module": "potentials",          # required
        "record_id": 123,                # required
        "kind": "contact_log",           # default if omitted
        "channel": "phone",              # optional classifier:
                                         #   phone / visit / wechat / im / other
        "summary": "客户确认 Q3 预算 80w",
        "body": "...",                   # optional longer text
        "next_contact_date": "2026-05-01",  # optional
        "user_id": 7,                    # inferred from auth in api.py
    }
"""
from __future__ import annotations

from typing import Any, Dict

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class ManualNoteAdapter(TouchpointAdapter):
    source = "manual"
    default_kind = "contact_log"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        kind = str(payload.get("kind") or self.default_kind)
        subjects = []
        mod = payload.get("module")
        rid = payload.get("record_id")
        if mod and rid is not None:
            subjects.append({"module": str(mod), "id": int(rid)})

        actors = []
        uid = payload.get("user_id")
        if uid:
            actor: Dict[str, Any] = {"type": "user", "id": int(uid)}
            pid = str(payload.get("profile_id") or "").strip()
            if pid:
                actor["profile_id"] = pid
            actors.append(actor)

        content: Dict[str, Any] = {}
        for key in ("summary", "body", "text", "title", "channel",
                    "next_contact_date", "duration_sec"):
            v = payload.get(key)
            if v is not None and v != "":
                content[key] = v
        # Preserve extra client-side metadata under a namespace.
        extras = payload.get("extra")
        if isinstance(extras, dict):
            content["extra"] = extras

        return NormalizedTouchpoint(
            kind=kind,
            content=content,
            source="manual",
            subjects=subjects,
            actors=actors,
        )


class ChatBoxAdapter(TouchpointAdapter):
    """In-app conversation with the agent.

    Each user turn writes one ``kind='chat'`` row; agent responses go
    in as ``kind='action'`` via ``bridge/events`` already. This keeps
    the full "对话即界面" transcript in the Log.
    """

    source = "chat"
    default_kind = "chat"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        content: Dict[str, Any] = {
            "text": payload.get("text") or payload.get("message") or "",
            "session_id": payload.get("session_id"),
            "role": payload.get("role", "user"),
        }
        actors = []
        uid = payload.get("user_id")
        if uid:
            actor: Dict[str, Any] = {"type": "user", "id": int(uid)}
            pid = str(payload.get("profile_id") or "").strip()
            if pid:
                actor["profile_id"] = pid
            actors.append(actor)
        subjects = []
        mod = payload.get("module")
        rid = payload.get("record_id")
        if mod and rid is not None:
            subjects.append({"module": str(mod), "id": int(rid)})
        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source="chat",
            subjects=subjects,
            actors=actors,
        )


register_adapter(ManualNoteAdapter())
register_adapter(ChatBoxAdapter())

__all__ = ["ManualNoteAdapter", "ChatBoxAdapter"]
