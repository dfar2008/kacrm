"""Surviving dashboard-semantic helper.

The old dashboard "mini-assistant" chat backend was retired in 002d00f
(2026-06-22). But the CRM agent's ``crm_dashboard`` tool (hermes-side,
``tools/crm/dispatch.py``) still imports ``_semantic_summary`` from here to
answer "list_entities" — the step the agent runs to discover which dimensions
and measures exist before it creates or configures a chart. Deleting the whole
module left that import dangling → ``ModuleNotFoundError`` → the tool failed and
"add/configure a chart via ChatInbox" silently broke.

This trimmed module keeps ONLY that one live dependency, so the agent's
dashboard-chart flow works without resurrecting the dead chat backend.
"""

from __future__ import annotations

from typing import Any, Dict, Set

from .semantic.registry import Registry


def _semantic_summary(registry: Registry, grants: Set[str]) -> Dict[str, Any]:
    """Compact summary of entities / dimensions / measures available to this
    caller, filtered by grants so the LLM doesn't hallucinate measures it can't
    use. Includes custom fields discovered at runtime via the active registry.
    """
    out: Dict[str, Any] = {}
    for name in registry.entity_names():
        e = registry.entity(name)
        out[name] = {
            "dimensions": [
                {
                    "name": d.name,
                    "type": d.type,
                    "values": list(d.values) if d.values else None,
                    "granularities": list(d.granularities) if d.granularities else None,
                }
                for d in e.dimensions
            ],
            "measures": [
                {"name": m.name, "type": m.type}
                for m in e.measures
                if not m.requires_grant or m.requires_grant in grants
            ],
        }
    return out
