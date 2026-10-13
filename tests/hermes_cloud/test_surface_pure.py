"""Pure-function tests for hermes_cloud.app.surface.

DB-touching helpers (entity_extract, lookup_user_tz) tested separately
in the PG integration suite. This file covers:

* SurfaceKind enum
* surface_acl_for() — per-surface ACL allowlist + None-for-chatbox
* derive_session_id() — day / thread / auto_drive variants + tz handling
* render_reply() routing
* feishu_card.render_* — output shape snapshots
* spa_render.render_spa_reply — chip extraction
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from hermes_cloud.app.surface import (
    SurfaceKind,
    derive_session_id,
    surface_acl_for,
    render_reply,
)
from hermes_cloud.app.surface.feishu_card import (
    render_feishu_alert,
    render_feishu_daily_completed,
    render_feishu_reply,
)
from hermes_cloud.app.surface.spa_render import render_spa_reply


# ──────────────────────────────────────────────────────────────────
# SurfaceKind
# ──────────────────────────────────────────────────────────────────


def test_surface_kind_values_stable():
    """Enum values are wire-format — anything that breaks these breaks
    the X-Hermes-Origin-Channel header on outbound requests, which
    breaks audit trails on the gateway_actions table."""
    assert SurfaceKind.SPA_CHATBOX.value == "spa_chatbox"
    assert SurfaceKind.SPA_RECORD_COPILOT.value == "spa_record_copilot"
    assert SurfaceKind.FEISHU.value == "feishu"
    assert SurfaceKind.ILINK.value == "wecom_ilink"
    assert SurfaceKind.AUTO_DRIVE.value == "auto_drive"


# ──────────────────────────────────────────────────────────────────
# surface_acl_for
# ──────────────────────────────────────────────────────────────────


def test_user_surfaces_are_trust_audit_not_whitelist():
    """All user-facing surfaces (SPA chat/copilot + IM feishu/ilink)
    return ACL=None — patch 27 falls back to allow-all. Safety comes
    from gateway revert_token, not pre-deny. See _ACL_PER_SURFACE
    docblock for the 2026-05-26 rationale."""
    for sk in (
        SurfaceKind.SPA_CHATBOX,
        SurfaceKind.SPA_RECORD_COPILOT,
        SurfaceKind.FEISHU,
        SurfaceKind.ILINK,
    ):
        assert surface_acl_for(sk) is None, sk


def test_auto_drive_acl_is_empty_list():
    """auto_drive is the only surface that stays locked: cron runs are
    unsupervised, mistakes nobody sees in real time. It calls skill
    Python entries directly (patch 26), not the LLM-driven tool loop —
    empty allowlist is the schelling fence."""
    acl = surface_acl_for(SurfaceKind.AUTO_DRIVE)
    assert acl is not None
    assert acl["allow"] == []
    assert acl["surface"] == "auto_drive"


# ──────────────────────────────────────────────────────────────────
# derive_session_id
# ──────────────────────────────────────────────────────────────────


def test_day_session_format():
    sid = derive_session_id(7, SurfaceKind.FEISHU, tz="Asia/Shanghai")
    # Format: user-{id}-day-YYYY-MM-DD
    assert sid.startswith("user-7-day-")
    parts = sid.split("user-7-day-", 1)[1]
    # parses as a YYYY-MM-DD date
    datetime.fromisoformat(parts)


def test_explicit_new_yields_thread_session():
    sid = derive_session_id(7, SurfaceKind.SPA_CHATBOX, explicit_new=True)
    assert sid.startswith("user-7-thread-")
    # 12 hex chars after the prefix
    suffix = sid.split("user-7-thread-", 1)[1]
    assert len(suffix) == 12
    int(suffix, 16)  # parses as hex


def test_explicit_new_thread_sessions_are_unique():
    """Each call must produce a fresh uuid suffix."""
    a = derive_session_id(7, SurfaceKind.SPA_CHATBOX, explicit_new=True)
    b = derive_session_id(7, SurfaceKind.SPA_CHATBOX, explicit_new=True)
    assert a != b


def test_record_copilot_scopes_session_by_record():
    """SPA_RECORD_COPILOT with a record_ref must produce a per-record
    session so hermes' agent_sessions history can't bleed accounts#427's
    contact name into accounts#426's reply (the 2026-05-26 cross-record
    contamination bug)."""
    a = derive_session_id(
        7, SurfaceKind.SPA_RECORD_COPILOT, tz="Asia/Shanghai",
        record_ref=("accounts", 426),
    )
    b = derive_session_id(
        7, SurfaceKind.SPA_RECORD_COPILOT, tz="Asia/Shanghai",
        record_ref=("accounts", 427),
    )
    assert a != b
    assert "rec-accounts-426" in a
    assert "rec-accounts-427" in b


def test_record_copilot_without_ref_falls_back_to_day_session():
    """Without record_ref (list-mode copilot) we fall back to the
    shared per-day session so list-level chat stays continuous."""
    sid = derive_session_id(
        7, SurfaceKind.SPA_RECORD_COPILOT, tz="Asia/Shanghai",
    )
    assert sid.startswith("user-7-day-")
    assert "rec-" not in sid


def test_record_copilot_same_record_same_day_reuses_session():
    """Same record + same day = same session (so multi-turn chat
    inside one record still accumulates context as expected)."""
    a = derive_session_id(
        7, SurfaceKind.SPA_RECORD_COPILOT, tz="Asia/Shanghai",
        record_ref=("accounts", 498),
    )
    b = derive_session_id(
        7, SurfaceKind.SPA_RECORD_COPILOT, tz="Asia/Shanghai",
        record_ref=("accounts", 498),
    )
    assert a == b


def test_auto_drive_uses_dedicated_session():
    sid = derive_session_id(7, SurfaceKind.AUTO_DRIVE)
    assert sid.startswith("user-7-auto-")


def test_auto_drive_ignores_explicit_new():
    """auto_drive's session is keyed on date only — explicit_new is
    a no-op so cron retries within the same day reuse the same session."""
    a = derive_session_id(7, SurfaceKind.AUTO_DRIVE, explicit_new=True)
    b = derive_session_id(7, SurfaceKind.AUTO_DRIVE, explicit_new=False)
    assert a == b


def test_same_day_same_user_cross_surface_share_session():
    """Charter §简化: SPA / 飞书 / iLink converge on one session per
    user per day so context accumulates."""
    spa = derive_session_id(7, SurfaceKind.SPA_CHATBOX, tz="UTC")
    feishu = derive_session_id(7, SurfaceKind.FEISHU, tz="UTC")
    ilink = derive_session_id(7, SurfaceKind.ILINK, tz="UTC")
    assert spa == feishu == ilink


def test_different_users_get_different_sessions():
    a = derive_session_id(7, SurfaceKind.FEISHU, tz="UTC")
    b = derive_session_id(8, SurfaceKind.FEISHU, tz="UTC")
    assert a != b


def test_garbled_tz_falls_back_to_default():
    """Bad timezone strings shouldn't crash; falls back to Asia/Shanghai."""
    sid = derive_session_id(7, SurfaceKind.FEISHU, tz="Not/A/Real/TZ")
    assert sid.startswith("user-7-day-")


# ──────────────────────────────────────────────────────────────────
# render_reply routing
# ──────────────────────────────────────────────────────────────────


def test_render_reply_routes_spa_to_markdown():
    out = render_reply(surface=SurfaceKind.SPA_CHATBOX, text="hi")
    assert out["kind"] == "markdown"


def test_render_reply_routes_feishu_to_card():
    out = render_reply(surface=SurfaceKind.FEISHU, text="hi")
    assert out["kind"] == "feishu_card"


def test_render_reply_routes_ilink_to_plain_text():
    out = render_reply(surface=SurfaceKind.ILINK, text="hi")
    assert out["kind"] == "plain_text"


# ──────────────────────────────────────────────────────────────────
# feishu_card snapshots
# ──────────────────────────────────────────────────────────────────


def test_feishu_reply_card_has_header_and_body():
    out = render_feishu_reply(text="hello", pending_actions=[])
    card = out["card"]
    assert card["header"]["title"]["content"].startswith("🤖")
    assert any(
        e.get("text", {}).get("content") == "hello"
        for e in card["elements"]
    )


def test_feishu_daily_completed_renders_n_items():
    items = [
        {"title": "起跟进邮件给麒麟汽车", "ref": "act-1"},
        {"title": "起催款邮件给海尔智家", "ref": "act-2"},
        {"title": "排了 MES 召回任务",   "ref": "act-3"},
    ]
    out = render_feishu_daily_completed(user_label="张三", items=items)
    card = out["card"]
    body = next(
        e["text"]["content"] for e in card["elements"]
        if e.get("tag") == "div"
    )
    assert "AI 已替你处理 **3** 件事" in body
    for it in items:
        assert it["title"] in body
    # Each item with ref → 2 buttons (看草稿 / 不发) → 6 buttons total
    actions = next(
        e for e in card["elements"] if e.get("tag") == "action"
    )
    assert len(actions["actions"]) == 6


def test_feishu_alert_card_optionally_includes_mute_button():
    out_no_mute = render_feishu_alert(signal_text="some pattern")
    elems = out_no_mute["card"]["elements"]
    assert not any(e.get("tag") == "action" for e in elems)

    out_with_mute = render_feishu_alert(
        signal_text="some pattern", mute_signal_id="industry_change",
    )
    actions = next(
        e for e in out_with_mute["card"]["elements"]
        if e.get("tag") == "action"
    )
    assert len(actions["actions"]) == 1
    assert "永远不再提醒" in actions["actions"][0]["text"]["content"]


# ──────────────────────────────────────────────────────────────────
# spa_render
# ──────────────────────────────────────────────────────────────────


def test_spa_render_extracts_action_chips_from_markdown():
    text = "好的，[让 AI 替你处理](action:approve:abc123) — 也可以 [取消](action:cancel:abc123)"
    out = render_spa_reply(text=text, pending_actions=[])
    assert out["kind"] == "markdown"
    chips = out["chips"]
    assert len(chips) == 2
    assert chips[0]["label"] == "让 AI 替你处理"
    assert chips[0]["kind"] == "approve"
    assert chips[0]["ref"] == "abc123"
    assert chips[1]["kind"] == "cancel"


def test_spa_render_appends_pending_actions_as_chips():
    out = render_spa_reply(
        text="ok",
        pending_actions=[{"label": "查看草稿", "kind": "view_draft", "ref": "d1"}],
    )
    assert any(
        c["label"] == "查看草稿" and c["kind"] == "view_draft"
        for c in out["chips"]
    )


def test_spa_render_passes_through_plain_markdown():
    out = render_spa_reply(text="**bold** text", pending_actions=[])
    assert out["markdown"] == "**bold** text"
    assert out["chips"] == []


# ──────────────────────────────────────────────────────────────────
# Charter v2 compliance — header values
# ──────────────────────────────────────────────────────────────────


def test_acl_header_value_is_compact_json_for_auto_drive():
    """patch 27 expects the X-Hermes-Surface-ACL header to fit comfortably
    in a header (HTTP servers cap at 4-8 KB). AUTO_DRIVE is the only
    surface still emitting ACL (others trust gateway revert)."""
    from hermes_cloud.app.surface.surface import inject_context
    import hermes_cloud.app.surface.session as session_mod
    saved = session_mod.lookup_user_tz
    session_mod.lookup_user_tz = lambda uid: "Asia/Shanghai"
    try:
        env = inject_context(
            text="hello",
            user_id=7,
            surface=SurfaceKind.AUTO_DRIVE,
        )
    finally:
        session_mod.lookup_user_tz = saved
    acl_hdr = env.headers.get("X-Hermes-Surface-ACL", "")
    assert acl_hdr
    assert len(acl_hdr.encode("utf-8")) < 200
    parsed = json.loads(acl_hdr)
    assert parsed["surface"] == "auto_drive"
    assert parsed["allow"] == []


def test_user_facing_surfaces_omit_acl_header():
    """SPA chatbox/copilot + IM (feishu/ilink) no longer emit
    X-Hermes-Surface-ACL — hermes patch 27 falls back to allow-all."""
    from hermes_cloud.app.surface.surface import inject_context
    import hermes_cloud.app.surface.session as session_mod
    saved = session_mod.lookup_user_tz
    session_mod.lookup_user_tz = lambda uid: "Asia/Shanghai"
    try:
        for sk in (
            SurfaceKind.SPA_CHATBOX,
            SurfaceKind.SPA_RECORD_COPILOT,
            SurfaceKind.FEISHU,
            SurfaceKind.ILINK,
        ):
            env = inject_context(text="hi", user_id=7, surface=sk)
            assert "X-Hermes-Surface-ACL" not in env.headers, sk
    finally:
        session_mod.lookup_user_tz = saved


def test_inject_context_omits_acl_for_chatbox():
    """Chatbox is back-compat permissive — no ACL header is set."""
    from hermes_cloud.app.surface.surface import inject_context
    import hermes_cloud.app.surface.session as session_mod
    saved = session_mod.lookup_user_tz
    session_mod.lookup_user_tz = lambda uid: "Asia/Shanghai"
    try:
        env = inject_context(
            text="hello", user_id=7, surface=SurfaceKind.SPA_CHATBOX,
        )
    finally:
        session_mod.lookup_user_tz = saved
    assert "X-Hermes-Surface-ACL" not in env.headers
    # Origin channel header still set
    assert env.headers["X-Hermes-Origin-Channel"] == "spa_chatbox"


def test_inject_context_record_ref_injects_summary():
    """When the Copilot opens on a specific record, the system message
    should include a one-liner summary of that record (so the LLM
    can't hallucinate the name from prior conversation)."""
    from hermes_cloud.app.surface.surface import inject_context
    import hermes_cloud.app.surface.session as session_mod
    import hermes_cloud.app.surface.surface as surf_mod

    saved_tz = session_mod.lookup_user_tz
    saved_summary = surf_mod._build_record_summary
    session_mod.lookup_user_tz = lambda uid: "Asia/Shanghai"
    surf_mod._build_record_summary = (
        lambda m, i: "客户名称=贵阳来顺贸易公司; 关键联系人=杨华·1388716688"
    )
    try:
        env = inject_context(
            text="杨总要 3 套",
            user_id=1,
            surface=SurfaceKind.SPA_RECORD_COPILOT,
            record_ref=("accounts", 498),
        )
    finally:
        session_mod.lookup_user_tz = saved_tz
        surf_mod._build_record_summary = saved_summary
    sys_msg = env.messages[0]
    assert sys_msg["role"] == "system"
    assert "accounts" in sys_msg["content"]
    assert "#498" in sys_msg["content"]
    assert "贵阳来顺贸易公司" in sys_msg["content"]
    assert "杨华" in sys_msg["content"]
    assert "不要凭对话历史" in sys_msg["content"]


def test_inject_context_record_ref_summary_failure_falls_back():
    """If the summary helper returns None (DB error, missing record),
    dispatch must still proceed — just without the summary line."""
    from hermes_cloud.app.surface.surface import inject_context
    import hermes_cloud.app.surface.session as session_mod
    import hermes_cloud.app.surface.surface as surf_mod

    saved_tz = session_mod.lookup_user_tz
    saved_summary = surf_mod._build_record_summary
    session_mod.lookup_user_tz = lambda uid: "Asia/Shanghai"
    surf_mod._build_record_summary = lambda m, i: None
    try:
        env = inject_context(
            text="hi",
            user_id=1,
            surface=SurfaceKind.SPA_RECORD_COPILOT,
            record_ref=("accounts", 999999),
        )
    finally:
        session_mod.lookup_user_tz = saved_tz
        surf_mod._build_record_summary = saved_summary
    sys_msg = env.messages[0]
    assert "#999999" in sys_msg["content"]
    assert "该记录核心字段" not in sys_msg["content"]


def test_inject_context_session_id_in_headers():
    from hermes_cloud.app.surface.surface import inject_context
    import hermes_cloud.app.surface.session as session_mod
    saved = session_mod.lookup_user_tz
    session_mod.lookup_user_tz = lambda uid: "UTC"
    try:
        env = inject_context(
            text="hi", user_id=42, surface=SurfaceKind.FEISHU,
        )
    finally:
        session_mod.lookup_user_tz = saved
    assert env.headers["X-Hermes-Session-Id"] == env.session_id
    assert env.session_id.startswith("user-42-day-")
