"""Reclassify adapter.

Turns one existing interaction into a derived child interaction with a
new kind (for example: chat -> call_log / contact_log).
"""
from __future__ import annotations

from typing import Any, Dict, List

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint,
    TouchpointAdapter,
    register_adapter,
)


class ReclassifyAdapter(TouchpointAdapter):
    source = "reclassify"
    default_kind = "contact_log"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        new_kind = str(payload.get("new_kind") or self.default_kind).strip()
        derived_from = str(payload.get("derived_from") or "").strip()
        if not new_kind:
            raise ValueError("new_kind is required")
        if not derived_from:
            raise ValueError("derived_from is required")

        subjects: List[Dict[str, Any]] = []
        source_subjects = payload.get("subjects")
        if isinstance(source_subjects, list):
            subjects = [s for s in source_subjects if isinstance(s, dict)]
        elif payload.get("module") and payload.get("record_id") is not None:
            subjects = [{
                "module": str(payload["module"]),
                "id": int(payload["record_id"]),
            }]

        actors: List[Dict[str, Any]] = []
        uid = payload.get("user_id")
        if uid is not None:
            actors.append({"type": "user", "id": int(uid)})

        content: Dict[str, Any] = {}
        for key in ("summary", "body", "text", "message", "channel", "reason"):
            value = payload.get(key)
            if value not in (None, ""):
                content[key] = value
        content["reclassified"] = True
        content["from_kind"] = payload.get("from_kind")

        return NormalizedTouchpoint(
            kind=new_kind,
            content=content,
            source=self.source,
            subjects=subjects,
            actors=actors,
            derived_from=derived_from,
        )


register_adapter(ReclassifyAdapter())

__all__ = ["ReclassifyAdapter"]
