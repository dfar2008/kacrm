"""Surface kind + ACL + inject_context + render_reply primitives.

The 4 ``SurfaceKind`` enum values map 1-1 with the per-surface tool
allowlists in ``_ACL_PER_SURFACE``. Adding a new surface = add an enum
value + add an allowlist entry; both pieces are needed or
:func:`surface_acl_for` returns ``None`` (no enforcement → patch 27
back-compat permissive — risky for new untrusted surfaces, fine for
the legacy chatbox).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SurfaceKind(str, Enum):
    """Origin surface of a hermes-bound request. Determines:

    * which ``X-Hermes-Origin-Channel`` value is set (patch 20)
    * which ``X-Hermes-Surface-ACL`` allowlist is set (patch 27)
    * how the reply text is rendered back to the user
    """

    SPA_CHATBOX = "spa_chatbox"
    SPA_RECORD_COPILOT = "spa_record_copilot"
    # P1 (retire-mini-assistants) — two scoped SPA panels that drive
    # the real per-profile agent. Both behave like SPA_CHATBOX for
    # session policy + ACL (trust + audit, no pre-deny); the scope
    # preamble in the message biases the agent to crm_schema /
    # crm_dashboard tools respectively.
    SPA_SCHEMA_ASSISTANT = "spa_schema_assistant"
    SPA_DASHBOARD_CHAT = "spa_dashboard_chat"
    FEISHU = "feishu"
    ILINK = "wecom_ilink"
    AUTO_DRIVE = "auto_drive"


# Per-surface tool allowlist. Conservative — start tight, expand if
# users hit "tool denied" repeatedly. Names match hermes' tool-dispatch
# function_name keys (todo / memory / session_search / clarify / plus
# the CRM tools registered by patch 24).
#
# A ``None`` entry = no ACL bound → patch 27 back-compat allow-all. Used
# only for the legacy chatbox during migration; new surfaces should
# Design (2026-05-26 — moved from whitelist to "trust + audit"):
# The original per-surface whitelists were defensive guesswork from
# before the gateway acquired revert_token. They consistently locked
# out core CRM use cases (e.g. blocking crm_task_create on the SPA
# record-page Copilot was the bug that prompted this rewrite).
#
# Real safety today comes from the gateway:
#   * every write goes through ``action_gateway/dispatcher`` →
#     persisted ``gateway_actions`` row with a ``revert_token``,
#   * SPA shows the chip with one-click revert,
#   * IM/auto-drive routes can replay the revert via the same token.
#
# So we trust + audit instead of pre-deny. The only surface that
# stays locked is ``AUTO_DRIVE``: cron runs are unsupervised, any
# mistake nobody sees in real time. Cron deliberately calls skill
# Python entries directly (patch 26), not the LLM-driven tool loop —
# the empty allowlist is the schelling fence.
#
# Tool names match hermes' real native CRM tool registry (patch 24
# ``tools/crm/dispatch.py``: crm_search / crm_get / crm_write /
# crm_note_create / crm_list_related / crm_list_modules / crm_get_schema /
# crm_task_create / crm_pending_approvals / crm_approve_action).
_ACL_PER_SURFACE: Dict[SurfaceKind, Optional[List[str]]] = {
    SurfaceKind.SPA_CHATBOX: None,
    SurfaceKind.SPA_RECORD_COPILOT: None,
    # P1 scoped panels — same trust+audit posture as the chatbox.
    SurfaceKind.SPA_SCHEMA_ASSISTANT: None,
    SurfaceKind.SPA_DASHBOARD_CHAT: None,
    SurfaceKind.FEISHU: None,
    SurfaceKind.ILINK: None,
    # auto_drive: only the skill Python-entry funcs (patch 26).
    # Empty allowlist is intentional — if anyone wires auto_drive into
    # the LLM tool loop, every tool call denies.
    SurfaceKind.AUTO_DRIVE: [],
}


def surface_acl_for(surface: SurfaceKind) -> Optional[Dict[str, Any]]:
    """Return the JSON-shaped ACL payload for a surface.

    ``None`` means "don't bind any ACL" — patch 27 falls back to its
    back-compat allow-all. Use sparingly; only for the legacy
    chatbox path during migration.
    """
    allow = _ACL_PER_SURFACE.get(surface)
    if allow is None:
        return None
    return {"surface": surface.value, "allow": list(allow)}


@dataclass
class HermesRequestEnvelope:
    """Everything the surface layer hands the proxy for one outbound
    hermes call. Stateless — the proxy is free to retry with the same
    envelope or drop it without bookkeeping."""

    surface: SurfaceKind
    user_id: int
    session_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)


_SUMMARY_MAX_CHARS = 220  # ~50 BPE tokens — keeps prompt cheap.
_SUMMARY_MAX_FIELDS = 8


def _build_user_identity(user_id: int) -> Optional[str]:
    """Current user's display name (realname → username), or None on any hiccup.

    Same ``COALESCE(realname, username)`` convention detail_query / the SPA use
    to render an owner. Failure is swallowed — a missing name just drops the
    identity system message rather than blocking dispatch.
    """
    try:
        from hermes.crm.db import db_session
    except Exception:
        return None
    try:
        with db_session() as db:
            row = db.fetchone(
                "SELECT COALESCE(NULLIF(realname, ''), username) AS display "
                "FROM users WHERE id = %s",
                (int(user_id),),
            )
        if row and row.get("display"):
            return str(row["display"]).strip() or None
    except Exception:
        return None
    return None


def _build_record_summary(module: str, record_id: int) -> Optional[str]:
    """Compact one-liner describing a record's headline fields.

    Pulls the first ``_SUMMARY_MAX_FIELDS`` ``is_default=true`` fields
    by ``sort_order`` (same set the SPA detail page shows in its top
    chip row) plus their current values, and — for ``accounts`` —
    the primary contact's name/mobile. Empty values are skipped.

    Failures swallowed: caller falls back to the ID-only system
    message rather than blocking dispatch on a metadata hiccup.
    """
    try:
        from hermes.crm.db import db_session
        from hermes.crm.models.modules_db import ModulesDb
    except Exception:
        return None
    try:
        with db_session() as db:
            mod = ModulesDb(db).get_module_by_name(module)
            if not mod:
                return None
            mid = int(mod["id"])
            table = str(mod["table_name"])
            pk = f"{table}_id"
            # Skip relation/user/department types — their stored value
            # is a bare FK id, which is noise to the LLM. The agent
            # already has crm_get_related to follow them when needed.
            fields = db.fetchall(
                "SELECT field_name, field_label "
                "  FROM module_fields "
                " WHERE module_id = %s AND is_default = TRUE "
                "   AND is_hidden = FALSE "
                "   AND field_type NOT IN "
                "       ('relation','user','department','formula', "
                "        'datetime','timestamp') "
                "   AND field_name NOT IN "
                "       ('created_at','updated_at','created_by', "
                "        'updated_by','owner_by','department_id', "
                "        'deleted') "
                " ORDER BY sort_order ASC, id ASC LIMIT %s",
                (mid, _SUMMARY_MAX_FIELDS),
            ) or []
            # Always include the {table}_name even if not flagged
            # default (some modules forget to mark it).
            name_col = f"{table}_name"
            if not any(f["field_name"] == name_col for f in fields):
                fields.insert(0, {
                    "field_name": name_col, "field_label": "名称",
                })
            cols = ", ".join(f'"{f["field_name"]}"' for f in fields)
            row = db.fetchone(
                f'SELECT {cols} FROM "{table}" WHERE {pk} = %s',
                (record_id,),
            )
            if not row:
                return None
            parts: list = []
            for f in fields:
                v = row.get(f["field_name"])
                if v is None or v == "" or v == 0:
                    continue
                parts.append(f'{f["field_label"]}={v}')
            if table == "accounts":
                contact = db.fetchone(
                    'SELECT contacts_name, mobile FROM contacts '
                    ' WHERE accounts_id = %s '
                    ' ORDER BY contacts_id ASC LIMIT 1',
                    (record_id,),
                )
                if contact and contact.get("contacts_name"):
                    label = contact["contacts_name"]
                    if contact.get("mobile"):
                        label += f"·{contact['mobile']}"
                    parts.append(f"关键联系人={label}")
            if not parts:
                return None
            summary = "; ".join(parts)
            if len(summary) > _SUMMARY_MAX_CHARS:
                summary = summary[: _SUMMARY_MAX_CHARS - 1] + "…"
            return summary
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "build_record_summary failed module=%s id=%s",
            module, record_id, exc_info=True,
        )
        return None


def inject_context(
    *,
    text: str,
    user_id: int,
    surface: SurfaceKind,
    record_ref: Optional[tuple] = None,
    explicit_new_session: bool = False,
    scope: str = "self",  # Phase 5.0 prep — see below
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> HermesRequestEnvelope:
    """Wrap ``text`` into a hermes-bound request envelope.

    First-cut surface plumbing (Day 6-7): packages the right session id +
    headers + a minimal message list. Later phases will enrich the
    system prompt with RecordInsightCard summary and ai_noticed
    awareness (Week 3). Today: just session + ACL + origin headers.

    Phase 5.1 (2026-05-09 plan v1.2 §4.2) — ``scope`` is now read.
    When ``scope != 'self'``, an ``X-Hermes-Scope`` header is added
    carrying ``{"kind": scope, "user_ids": [...]}`` so hermes patch
    30 can apply ``WHERE owner_by = ANY(...)`` filters at the SQL
    layer. The ContextVar pattern (same as patch 27 surface_acl)
    ensures the LLM never sees the user_id list — pure dispatch
    enforcement.
    """
    # Local imports avoid circular module deps + lazy-load heavy bits.
    from hermes_cloud.app.surface.session import (
        derive_session_id, lookup_user_tz,
    )

    tz = lookup_user_tz(user_id)
    session_id = derive_session_id(
        user_id, surface,
        explicit_new=explicit_new_session, tz=tz,
        record_ref=record_ref,
    )

    headers: Dict[str, str] = {
        "X-Hermes-Session-Id": session_id,
        "X-Hermes-Origin-Channel": surface.value,
    }
    acl = surface_acl_for(surface)
    if acl is not None:
        # Compact JSON — patch 27 expects the header value to be parseable.
        headers["X-Hermes-Surface-ACL"] = json.dumps(
            acl, ensure_ascii=False, separators=(",", ":"),
        )

    # Phase 5.1 — if scope != 'self', resolve viewable_user_ids and
    # bind via X-Hermes-Scope header (hermes patch 30 reads it). For
    # 'self' scope we don't emit the header so legacy behavior is
    # preserved (hermes' default-deny on missing header is gated
    # behind a feature flag — TODO: confirm with patch 30 ContextVar).
    if scope and scope != "self":
        try:
            from hermes_cloud.app.scope import viewable_user_ids
            from hermes_cloud.app.scope.audit import record_scope_access
            from hermes.crm.db import db_session
            with db_session() as _scope_db:
                allowed = viewable_user_ids(
                    _scope_db, caller_id=int(user_id), scope=scope,
                )
                # Plan v1.2 §7 CI #3: scope='team'/'org' must audit_log.
                record_scope_access(
                    _scope_db, caller_id=int(user_id),
                    scope_kind=scope, resource_kind="surface_dispatch",
                    resource_filter={"surface": surface.value},
                )
                _scope_db.commit()
            headers["X-Hermes-Scope"] = json.dumps(
                {"kind": scope, "user_ids": sorted(allowed)},
                ensure_ascii=False, separators=(",", ":"),
            )
        except Exception:
            # Never block dispatch on scope resolution; falls back to
            # 'self' behavior on hermes side (no header → no expansion)
            import logging as _l
            _l.getLogger(__name__).warning(
                "scope resolve failed user_id=%s scope=%s — falling back to self",
                user_id, scope, exc_info=True,
            )

    # Build the message list. When a record_ref is given, we prepend
    # a system message that pins the agent to "current record" AND
    # gives it a compact summary of the record's headline fields, so
    # the LLM doesn't have to call crm_get just to know whose page
    # the user is on — and (critical) won't hallucinate the name from
    # leftover conversation memory.
    #
    # Why the summary: without it, a prompt that said only
    # "accounts#498" let the LLM pull a name out of recent dialogue
    # ("字节跳动" from a prior test session) and stamp it onto a new
    # potentials row. The summary closes that gap at the cheapest
    # possible layer — one short SELECT before dispatch.
    messages: List[Dict[str, Any]] = []
    # Ground the agent in WHO it is talking to. user_id reaches the data layer
    # for RBAC, but the agent's PROMPT never learns the current user's identity —
    # so it pulls a name out of memory/history (e.g. greeting a stranger as
    # "丁总", then filtering CRM by that guessed name, which matches nothing).
    # One short SELECT closes that gap, mirroring the record-ref
    # anti-hallucination system message below.
    identity = _build_user_identity(user_id)
    if identity:
        messages.append({
            "role": "system",
            "content": (
                f"当前对话的登录用户是「{identity}」(CRM user_id={user_id})。"
                f"当用户说\"我/我的/我负责的/我这边\"时,指的就是此人。"
                f"**不要凭对话历史或模型记忆猜测用户的姓名或称呼** — 需要称呼时"
                f"使用上面这个名字。要查该用户自己的 CRM 记录时(如\"我今天有什么"
                f"要处理\"),调用 crm_search 时传 owner=\"me\"(服务端会解析成当前"
                f"用户),**不要把人名当作 search 关键词**。"
            ),
        })
    if record_ref:
        try:
            module, record_id = record_ref
        except (ValueError, TypeError):
            module, record_id = None, None
        if module and record_id:
            summary = _build_record_summary(module, int(record_id))
            summary_line = f"\n该记录核心字段:{summary}\n" if summary else ""
            messages.append({
                "role": "system",
                "content": (
                    f"用户当前在 {module} 模块的记录 #{record_id} 页面上。"
                    f"{summary_line}"
                    f"当用户说\"这条/这个/它/上面的\"等指代词,默认指向 "
                    f"{module}#{record_id}。**不要凭对话历史或模型记忆推断"
                    f"该记录的字段值** — 如需引用 system 摘要里未列出的"
                    f"字段,请调用 crm_get(module={module}, id={record_id}) "
                    f"实时查询;如需修改请用 update_record /"
                    f"list_related_records。"
                ),
            })
    # Attachments (images → data_url blocks, documents → text appended)
    # turn the user message into multimodal content via the same helper
    # the /v1/chat/completions path uses. With no attachments the content
    # stays a plain string so non-vision providers are unaffected.
    from hermes_cloud.app.llm.attachments import build_user_content
    messages.append({
        "role": "user",
        "content": build_user_content(text, attachments),
    })
    return HermesRequestEnvelope(
        surface=surface,
        user_id=int(user_id),
        session_id=session_id,
        messages=messages,
        headers=headers,
    )


def render_reply(
    *,
    surface: SurfaceKind,
    text: str,
    pending_actions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Format hermes' reply for the originating surface.

    Returns a dict with ``kind`` (``"markdown"`` / ``"feishu_card"`` /
    ``"plain_text"``) and surface-appropriate payload fields. Each
    surface's bridge / SPA panel reads ``kind`` to pick its renderer.
    """
    pending_actions = pending_actions or []
    if surface in (
        SurfaceKind.SPA_CHATBOX,
        SurfaceKind.SPA_RECORD_COPILOT,
        SurfaceKind.SPA_SCHEMA_ASSISTANT,
        SurfaceKind.SPA_DASHBOARD_CHAT,
    ):
        from hermes_cloud.app.surface.spa_render import render_spa_reply
        return render_spa_reply(text=text, pending_actions=pending_actions)
    if surface == SurfaceKind.FEISHU:
        from hermes_cloud.app.surface.feishu_card import render_feishu_reply
        return render_feishu_reply(text=text, pending_actions=pending_actions)
    if surface == SurfaceKind.ILINK:
        # Phase 4.5 — placeholder pass-through; ilink_text.py wired then.
        return {"kind": "plain_text", "text": text}
    # AUTO_DRIVE has no human-facing reply; caller writes to crm_drafts /
    # gateway_actions directly. Render is a no-op.
    return {"kind": "plain_text", "text": text}
