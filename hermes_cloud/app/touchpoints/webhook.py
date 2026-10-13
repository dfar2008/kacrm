"""Generic webhook adapter — escape hatch for any 3rd-party source.

Use this when a system doesn't have (or doesn't warrant) a dedicated
adapter. The payload is stored verbatim under ``content.raw`` and
the caller is expected to pass ``subjects`` explicitly.

Example — hook up Jira, Linear, Salesforce, HubSpot, a custom
internal CRM, whatever::

    POST /api/touchpoints/ingest/webhook
    {
        "source_name": "jira",
        "kind": "ticket_update",
        "subjects": [{"module": "potentials", "id": 42}],
        "raw": { ...whatever the 3rd party sent... }
    }
"""
from __future__ import annotations

from typing import Any, Dict

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class GenericWebhookAdapter(TouchpointAdapter):
    source = "webhook"
    default_kind = "webhook"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        kind = str(payload.get("kind") or self.default_kind)
        source_name = str(payload.get("source_name") or "unknown")

        content = {
            "source_name": source_name,
            "raw": payload.get("raw", payload),
        }
        # Lift a few well-known keys so downstream agents see the
        # usual text field.
        for key in ("text", "summary", "title", "message", "body"):
            v = payload.get(key)
            if v is not None:
                content[key] = v

        actors = []
        if payload.get("bound_user_id"):
            actors.append({"type": "user", "id": int(payload["bound_user_id"])})

        return NormalizedTouchpoint(
            kind=kind,
            content=content,
            source=f"webhook:{source_name}",
            subjects=list(payload.get("subjects") or []),
            actors=actors,
            provenance={"channel": "webhook", "source_name": source_name},
        )


register_adapter(GenericWebhookAdapter())

__all__ = ["GenericWebhookAdapter"]
