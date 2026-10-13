"""Voice memo touchpoint — sales dictation / voice note.

A short, sales-facing audio artifact: walking out of a meeting, the
sales rep records "王总要先看 demo，下周二约上" into their phone and
it becomes one :kind:`voice_memo` interaction.

The adapter is deliberately **STT-agnostic** — it takes an already
transcribed payload. Whoever uploads (a mobile app, a scheduled job
that polls Whisper, a 飞书妙记 webhook) handles the audio-to-text
step and then POSTs here. This keeps the touchpoint layer free of
heavy ML deps.

Expected payload (``POST /api/touchpoints/ingest/voice_memo``)::

    {
        "module": "potentials",          # optional subject
        "record_id": 123,                # optional subject
        "transcript": "王总说 Q3 要上新系统…",   # required-ish
        "duration_sec": 47,              # optional
        "language": "zh-CN",             # optional
        "audio_url": "s3://…/memo.m4a",  # optional, preserved as content.audio_url
        "provider": "whisper",           # optional: whisper / miaoji / manual
    }
"""
from __future__ import annotations

from typing import Any, Dict

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


class VoiceMemoAdapter(TouchpointAdapter):
    source = "voice_memo"
    default_kind = "voice_memo"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        transcript = (
            payload.get("transcript")
            or payload.get("text")
            or payload.get("body")
            or ""
        )
        content: Dict[str, Any] = {"text": str(transcript)}
        # Lift a short summary so Memory ingest + latest_progress work.
        summary = payload.get("summary") or (str(transcript)[:200] if transcript else "")
        if summary:
            content["summary"] = summary
        for key in ("duration_sec", "language", "audio_url", "provider"):
            v = payload.get(key)
            if v is not None and v != "":
                content[key] = v
        if isinstance(payload.get("extra"), dict):
            content["extra"] = payload["extra"]

        actors = []
        uid = payload.get("user_id")
        if uid:
            actors.append({"type": "user", "id": int(uid)})

        subjects = []
        mod = payload.get("module")
        rid = payload.get("record_id")
        if mod and rid is not None:
            subjects.append({"module": str(mod), "id": int(rid)})

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source="voice_memo",
            subjects=subjects,
            actors=actors,
        )


register_adapter(VoiceMemoAdapter())

__all__ = ["VoiceMemoAdapter"]
