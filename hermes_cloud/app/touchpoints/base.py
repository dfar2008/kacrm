"""Touchpoint adapter contract.

A :class:`TouchpointAdapter` is the thinnest possible layer between a
channel's native payload and the Interaction Log. The pattern:

1. Receive a channel payload (webhook body, long-connection event,
   form post, STT transcript, ...).
2. :meth:`normalize` returns a :class:`NormalizedTouchpoint` — actors,
   subjects, content, kind, source, timestamp.
3. :func:`ingest` runs the normalize + writes through the single
   emitter.

Subjects resolution (which CRM record does this touchpoint belong to?)
is either:

- **explicit** — webhook payload carries ``(module, record_id)``
- **heuristic** — adapter looks up by sender email / phone / wx_id
  against CRM contact identifiers

Heuristic resolution lives on each adapter (some channels expose a
phone, some an openid, some nothing at all); the base class just
accepts whatever subjects the adapter decided.

Adapters never touch the Memory Substrate or Agent Pool directly.
Their single responsibility is *write one row to interactions*.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from hermes.crm.db import DbWrapper
from hermes_cloud.app.interactions.emitter import emit

logger = logging.getLogger(__name__)


@dataclass
class NormalizedTouchpoint:
    """The canonical shape every adapter produces."""

    kind: str
    content: Dict[str, Any]
    source: str
    subjects: List[Dict[str, Any]] = field(default_factory=list)
    actors: List[Dict[str, Any]] = field(default_factory=list)
    occurred_at: Optional[datetime] = None
    provenance: Optional[Dict[str, Any]] = None
    derived_from: Optional[str] = None


class TouchpointAdapter:
    """Subclass this for each channel. See module docstring."""

    source: ClassVar[str] = ""           # 'channel:feishu' etc.
    default_kind: ClassVar[str] = "im_message"

    def normalize(self, payload: Dict[str, Any]) -> NormalizedTouchpoint:
        raise NotImplementedError

    def ingest(
        self,
        payload: Dict[str, Any],
        *,
        db: Optional[DbWrapper] = None,
    ) -> str:
        tp = self.normalize(payload)
        interaction_id = emit(
            kind=tp.kind,
            subjects=tp.subjects or None,
            actors=tp.actors or None,
            content=tp.content,
            source=tp.source or self.source,
            occurred_at=tp.occurred_at,
            provenance=tp.provenance,
            derived_from=tp.derived_from,
            db=db,
        )
        # Fire-and-forget LLM summary (no-op unless HERMES_AUTO_SUMMARIZE=1).
        # Skipped silently if the adapter already populated content.summary.
        try:
            from hermes_cloud.app.llm import schedule_interaction_summary
            schedule_interaction_summary(
                interaction_id,
                content=tp.content,
                kind=tp.kind,
            )
        except Exception:
            logger.exception("schedule_interaction_summary failed")
        return interaction_id


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: Dict[str, TouchpointAdapter] = {}


def register_adapter(adapter: TouchpointAdapter) -> None:
    if not adapter.source:
        raise ValueError(f"{type(adapter).__name__}.source must be set")
    _registry[adapter.source] = adapter


def get_adapter(source: str) -> Optional[TouchpointAdapter]:
    return _registry.get(source)


def list_adapters() -> List[str]:
    return sorted(_registry.keys())


def ingest(
    source: str,
    payload: Dict[str, Any],
    *,
    db: Optional[DbWrapper] = None,
) -> str:
    """Convenience helper: resolve adapter and ingest.

    Raises :class:`KeyError` if *source* isn't registered.
    """
    adapter = _registry.get(source)
    if adapter is None:
        raise KeyError(f"unknown touchpoint source: {source!r}")
    return adapter.ingest(payload, db=db)


__all__ = [
    "NormalizedTouchpoint",
    "TouchpointAdapter",
    "register_adapter",
    "get_adapter",
    "list_adapters",
    "ingest",
]
