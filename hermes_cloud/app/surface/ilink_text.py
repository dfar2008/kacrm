"""Render hermes replies for the wecom iLink (企业微信外部联系人) channel.

Phase 4 plan v2.2 §Week 5 Day 1-3 — iLink is text-only IM. No cards,
no buttons, no rich layout. The renderer produces a compact plain-text
envelope, with optional pending-action chips appended as numbered
lines that the user replies to with the corresponding number to
trigger the action.

Shape (matches ``surface.spa_render`` / ``feishu_card`` siblings):

    {"kind": "ilink_text", "text": "...", "chips": [{...}, ...]}

The bridge sends ``text`` via wecom send_text and stashes ``chips``
into ``channel_quick_replies`` so a later reply ("1" / "2") is
resolvable to the original action.
"""
from __future__ import annotations

from typing import Any, Dict, List


def render_ilink_reply(
    *, text: str, pending_actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Format ``text`` for iLink IM.

    iLink doesn't render markdown reliably across vendors — strip the
    fanciest bits and let the body breathe with line breaks. If
    ``pending_actions`` are present, append them as a numbered tail
    so the user can reply with a single digit.
    """
    body = (text or "").strip()
    chips: List[Dict[str, Any]] = []
    lines = [body] if body else []

    if pending_actions:
        lines.append("")
        lines.append("回复对应数字：")
        for idx, act in enumerate(pending_actions, start=1):
            if not isinstance(act, dict):
                continue
            label = str(act.get("label") or "").strip() or "操作"
            lines.append(f"{idx}. {label}")
            chips.append({
                "slot": idx,
                "kind": str(act.get("kind") or ""),
                "ref": str(act.get("ref") or ""),
                "label": label,
            })

    return {
        "kind": "ilink_text",
        "text": "\n".join(lines),
        "chips": chips,
    }


def render_ilink_daily_completed(
    *, user_label: str, items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """iLink-flavoured daily-completed envelope.

    Shape parallels ``feishu_card.render_feishu_daily_completed`` but
    stays text-only. Each action becomes a body line + a slot 1..N
    that the user can reply with to ``cancel`` (the most useful action
    for an unreviewed AI draft on a small-screen channel)."""
    n = len(items)
    lines = [f"早上好，{user_label}。AI 已替你处理 {n} 件事，6 小时后自动执行。"]
    chips: List[Dict[str, Any]] = []
    for idx, it in enumerate(items, start=1):
        title = str(it.get("title") or "(untitled)")
        ref = str(it.get("ref") or "")
        lines.append(f"{idx}. {title}")
        if ref:
            chips.append({
                "slot": idx, "kind": "cancel", "ref": ref, "label": title,
            })
    if chips:
        lines.append("")
        lines.append("回复 “不发 N” 取消第 N 项。")
    return {
        "kind": "ilink_text",
        "text": "\n".join(lines),
        "chips": chips,
    }


def render_ilink_alert(
    *, signal_text: str, mute_signal_id: str = "",
) -> Dict[str, Any]:
    """iLink alert text — flag emoji + body + optional mute prompt."""
    lines = ["🎓 " + (signal_text or "").strip()]
    chips: List[Dict[str, Any]] = []
    if mute_signal_id:
        lines.append("")
        lines.append("回复 “不再提醒” 永远屏蔽该类提示。")
        chips.append({
            "slot": 1, "kind": "mute_signal",
            "ref": mute_signal_id, "label": "不再提醒",
        })
    return {
        "kind": "ilink_text",
        "text": "\n".join(lines),
        "chips": chips,
    }
