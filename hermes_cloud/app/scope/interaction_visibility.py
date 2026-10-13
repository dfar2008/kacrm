"""Visibility filter for interactions rows.

Honors ``provenance.visibility`` JSONB field. Decision messages set this
to 'self_only' (charter §六软隔离 / spec §5.3). All consumers of
interactions SELECT results MUST run their rows through this filter,
or wire admin_forensic=True for compliance/audit views.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


def respect_interaction_visibility(
    rows: Iterable[Dict[str, Any]],
    *,
    caller_id: int,
    admin_forensic: bool = False,
) -> List[Dict[str, Any]]:
    """Filter rows whose provenance.visibility excludes caller.

    Rules:
    - No provenance.visibility → public, included
    - visibility='self_only' → only actor sees it
    - admin_forensic=True → bypass filter (audit/compliance)
    """
    if admin_forensic:
        return list(rows)

    out: List[Dict[str, Any]] = []
    for r in rows:
        prov = r.get("provenance") or {}
        vis = prov.get("visibility") if isinstance(prov, dict) else None
        if vis == "self_only":
            actors = r.get("actors") or []
            actor_id = (
                actors[0].get("id")
                if actors and isinstance(actors[0], dict)
                else None
            )
            if actor_id is None or int(actor_id) != int(caller_id):
                continue
        out.append(r)
    return out
