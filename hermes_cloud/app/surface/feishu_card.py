"""Render hermes replies + auto_drive pushes as 飞书 interactive cards.

Three card kinds:

* ``daily_completed`` — "AI 已替你处理 N 件事" + per-item revert chips.
* ``alert`` — high-severity ai_noticed signal that AI already acted on.
* ``reply`` — Copilot answer to a user's question, with optional
  pending-action chips below the body.

Schema follows 飞书 v2 interactive card JSON. We keep it minimal —
header + body markdown + actions block. Heavier templates (tables,
multi-column layouts) can layer on later without API changes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def render_feishu_reply(
    *, text: str, pending_actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Reply card for a Copilot answer."""
    return {
        "kind": "feishu_card",
        "card": _card(
            header_title="🤖 Copilot",
            body_md=text or "",
            actions=_chips_to_actions(pending_actions),
        ),
    }


def render_feishu_daily_completed(
    *,
    user_label: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Daily push: 'AI 已替你处理 N 件事' card.

    Each item should have ``title`` + ``ref`` (action_id) + optional
    ``draft_url``; we render an item line + 3 chips ([看草稿] [现在就发]
    [不发]).
    """
    n = len(items)
    body_lines = [
        f"早上好，{user_label}。AI 已替你处理 **{n}** 件事，6h 后自动执行。",
        "",
    ]
    actions: List[Dict[str, Any]] = []
    for idx, it in enumerate(items, start=1):
        title = it.get("title") or "(untitled)"
        body_lines.append(f"{idx}. {title}")
        ref = it.get("ref") or ""
        if ref:
            actions.extend([
                {"label": f"{idx}. 看草稿", "kind": "view_draft", "ref": ref},
                {"label": f"{idx}. 不发",  "kind": "cancel",     "ref": ref},
            ])
    return {
        "kind": "feishu_card",
        "card": _card(
            header_title="🌅 今日 AI 完成清单",
            body_md="\n".join(body_lines),
            actions=_chips_to_actions(actions),
        ),
    }


def render_feishu_alert(
    *, signal_text: str, mute_signal_id: Optional[str] = None,
) -> Dict[str, Any]:
    """High-severity ai_noticed alert — AI already acted on it; user
    sees the result + revert / mute options."""
    actions = []
    if mute_signal_id:
        actions.append(
            {"label": "永远不再提醒此类", "kind": "mute_signal", "ref": mute_signal_id}
        )
    return {
        "kind": "feishu_card",
        "card": _card(
            header_title="🎓 AI 发现并处理了一个跨记录模式",
            body_md=signal_text,
            actions=_chips_to_actions(actions),
        ),
    }


def _card(
    *, header_title: str, body_md: str, actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the canonical 飞书 interactive card JSON shell."""
    elements: List[Dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body_md}}
    ]
    if actions:
        elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": header_title},
        },
        "elements": elements,
    }


def _chips_to_actions(chips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert generic chip dicts into 飞书 button actions. Each chip
    becomes one button; the ``kind`` + ``ref`` are stuffed into
    ``value`` so the bridge's webhook handler can route on click."""
    out: List[Dict[str, Any]] = []
    for c in chips or []:
        if not isinstance(c, dict):
            continue
        label = str(c.get("label") or "").strip()
        if not label:
            continue
        out.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": "default",
            "value": {
                "kind": str(c.get("kind") or ""),
                "ref": str(c.get("ref") or ""),
            },
        })
    return out
