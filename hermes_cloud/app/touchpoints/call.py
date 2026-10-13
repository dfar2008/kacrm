"""Phone / video-call touchpoint — transcript of a human-to-human call.

Kind ``call_transcript`` covers any synchronous voice interaction:
outbound sales call, customer-success video meeting, inbound support
call. Transcripts come from whichever platform the user is on — 飞书
妙记, 腾讯会议 AI, 声网, Zoom, Twilio/Aircall/RingCentral, etc.
Like :mod:`voice_memo` this adapter is STT-agnostic: upstream
provides the already-transcribed text.

Expected payload (``POST /api/touchpoints/ingest/call``)::

    {
        "module": "potentials",
        "record_id": 123,
        "transcript": "<full dialogue>",    # either this …
        "segments": [                       # … or structured segments
            {"speaker": "sales", "text": "...", "start_sec": 0.0},
            {"speaker": "customer", "text": "...", "start_sec": 4.2},
        ],
        "participants": [                   # optional
            {"role": "sales", "user_id": 7},
            {"role": "customer", "name": "王总", "phone": "+86…"},
        ],
        "duration_sec": 2730,
        "provider": "feishu.miaoji",        # miaoji / tencent / zoom / twilio / manual
        "call_id": "<provider-specific>",
        "direction": "outbound",            # inbound / outbound
        "started_at": "2026-04-17T10:30:00+08:00",
        "recording_url": "s3://…",          # optional
    }
"""
from __future__ import annotations

from typing import Any, Dict, List

from hermes_cloud.app.touchpoints.base import (
    NormalizedTouchpoint, TouchpointAdapter, register_adapter,
)


def _flatten_segments(segments: List[Dict[str, Any]]) -> str:
    """Render a list of {speaker, text} objects into a single block."""
    parts: List[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        speaker = str(seg.get("speaker") or "").strip()
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"{speaker}: {text}" if speaker else text)
    return "\n".join(parts)


class CallTranscriptAdapter(TouchpointAdapter):
    source = "call"
    default_kind = "call_transcript"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        transcript = str(payload.get("transcript") or "").strip()
        segments = payload.get("segments")
        if not transcript and isinstance(segments, list):
            transcript = _flatten_segments(segments)

        content: Dict[str, Any] = {"transcript": transcript}
        if isinstance(segments, list):
            content["segments"] = segments
        summary = payload.get("summary") or (transcript[:200] if transcript else "")
        if summary:
            content["summary"] = summary
        for key in ("duration_sec", "provider", "call_id", "direction",
                    "started_at", "ended_at", "recording_url", "language"):
            v = payload.get(key)
            if v is not None and v != "":
                content[key] = v
        if isinstance(payload.get("extra"), dict):
            content["extra"] = payload["extra"]

        # Actors: the sales rep (internal user) + any external participants.
        actors: List[Dict[str, Any]] = []
        uid = payload.get("user_id")
        if uid:
            actors.append({"type": "user", "id": int(uid)})
        participants = payload.get("participants") or []
        if isinstance(participants, list):
            for p in participants:
                if not isinstance(p, dict):
                    continue
                if p.get("user_id"):
                    actors.append({"type": "user", "id": int(p["user_id"]),
                                   "role": p.get("role")})
                elif p.get("name") or p.get("phone"):
                    actors.append({
                        "type": "external",
                        "name": p.get("name"),
                        "phone": p.get("phone"),
                        "role": p.get("role"),
                    })

        subjects: List[Dict[str, Any]] = []
        mod = payload.get("module")
        rid = payload.get("record_id")
        if mod and rid is not None:
            subjects.append({"module": str(mod), "id": int(rid)})

        return NormalizedTouchpoint(
            kind=self.default_kind,
            content=content,
            source="call",
            subjects=subjects,
            actors=actors,
        )


register_adapter(CallTranscriptAdapter())

__all__ = ["CallTranscriptAdapter"]
