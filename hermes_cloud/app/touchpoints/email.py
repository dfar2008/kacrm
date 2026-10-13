"""Inbound email adapter.

Takes an already-parsed email envelope (headers + body). Pairing
with an IMAP poller / SMTP-receiver belongs in a separate worker;
this adapter only normalises the shape.

Shape::

    {
        "from":     "alice@acme.com",
        "to":       ["sales@ourco.com"],
        "cc":       [...],
        "subject":  "Re: proposal",
        "text":     "...",                   # plain-text body
        "html":     "...",                   # optional
        "message_id": "<xyz@acme.com>",
        "in_reply_to": "<abc@ourco.com>",
        "date":     "2026-04-17T09:12:33Z",  # RFC3339
        "bound_user_id": 7,
        "subjects": [...]
    }
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from hermes_cloud.app.touchpoints._im import resolve_subjects_by_identifier
from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class EmailAdapter(TouchpointAdapter):
    source = "channel:email"
    default_kind = "email"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        date_raw = payload.get("date")
        occurred_at = None
        if date_raw:
            try:
                occurred_at = datetime.fromisoformat(
                    str(date_raw).replace("Z", "+00:00")
                )
            except Exception:
                pass

        from_addr = str(payload.get("from") or "")

        content = {
            "platform": "email",
            "from": from_addr,
            "to": payload.get("to") or [],
            "cc": payload.get("cc") or [],
            "subject": str(payload.get("subject") or ""),
            "text": str(payload.get("text") or ""),
            "html": payload.get("html"),
            "message_id": payload.get("message_id"),
            "in_reply_to": payload.get("in_reply_to"),
        }

        actors = []
        if payload.get("bound_user_id"):
            actors.append({"type": "user", "id": int(payload["bound_user_id"])})
        if from_addr:
            actors.append({
                "type": "external",
                "platform": "email",
                "address": from_addr,
            })

        # Auto-resolve subjects by sender email if caller didn't pass any.
        subjects = list(payload.get("subjects") or [])
        if not subjects and from_addr:
            subjects = resolve_subjects_by_identifier(email=from_addr)

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source=self.source,
            subjects=subjects,
            actors=actors,
            occurred_at=occurred_at,
            provenance={"channel": "email"},
        )


register_adapter(EmailAdapter())

__all__ = ["EmailAdapter"]
