"""Render hermes' reply for the SPA Copilot panel (markdown + chips)."""
from __future__ import annotations

import re
from typing import Any, Dict, List


# Recognize ``[label](action:approve:abc12345)`` style action chips that
# hermes might emit. Phase 4.5 may evolve this; for now the SPA renders
# action-chip links as buttons, plain links as plain links.
_ACTION_CHIP_RE = re.compile(
    r"\[([^\]]+)\]\(action:([a-z_]+):([a-zA-Z0-9_-]+)\)",
)


def render_spa_reply(
    *, text: str, pending_actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Format ``text`` for the SPA Copilot panel.

    Returns ``{"kind": "markdown", "markdown": ..., "chips": [...]}``.
    The SPA renders ``markdown`` directly and shows ``chips`` as
    above-the-input action buttons.
    """
    chips: List[Dict[str, str]] = []
    for m in _ACTION_CHIP_RE.finditer(text or ""):
        chips.append({
            "label": m.group(1),
            "kind": m.group(2),
            "ref": m.group(3),
        })
    # Surface pending_actions (auto_drive / approval flow) as additional chips.
    for act in pending_actions:
        if not isinstance(act, dict):
            continue
        chips.append({
            "label": act.get("label") or "Action",
            "kind": act.get("kind") or "approve",
            "ref": act.get("ref") or "",
        })
    return {"kind": "markdown", "markdown": text or "", "chips": chips}
