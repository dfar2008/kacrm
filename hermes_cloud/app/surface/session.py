"""Cross-surface session-id derivation (Phase 4 §4.7).

Per the plan, "one user, one day, one session" — SPA / 飞书 / iLink /
chatbox / auto_drive all converge on the same hermes session id when
the request is from the same user on the same calendar day in their
timezone, so context accumulates naturally across surfaces.

* ``derive_session_id(user_id, surface)`` → today's main session
  ``"user-{user_id}-day-{YYYY-MM-DD}"``.
* ``derive_session_id(... explicit_new=True)`` → fresh thread session
  ``"user-{user_id}-thread-{12-hex}"`` — bound to the SPA "新对话"
  button or the IM "新对话" / "new chat" text command.
* ``auto_drive`` uses a dedicated session ``"user-{user_id}-auto-{YYYY-MM-DD}"``
  so its mass-drafted output doesn't pollute the user's chat scrollback.

Timezone: pulled from ``user_channel_prefs.timezone`` if a preferences
row exists for any of the user's bound channels; falls back to the
plan-default ``"Asia/Shanghai"`` (matches alembic 0049).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from hermes_cloud.app.surface.surface import SurfaceKind


_FALLBACK_TZ = "Asia/Shanghai"


def _today_in_tz(tz: str) -> str:
    """Return ``YYYY-MM-DD`` in the given IANA tz."""
    import zoneinfo
    try:
        zone = zoneinfo.ZoneInfo(tz)
    except Exception:
        zone = zoneinfo.ZoneInfo(_FALLBACK_TZ)
    return datetime.now(tz=zone).date().isoformat()


def derive_session_id(
    user_id: int,
    surface: SurfaceKind,
    *,
    explicit_new: bool = False,
    tz: Optional[str] = None,
    dept_id: Optional[int] = None,  # Phase 5.0 prep — see below
    record_ref: Optional[tuple] = None,
) -> str:
    """Decide which hermes session id ``(user_id, surface)`` should use.

    ``explicit_new=True`` triggers a fresh thread (e.g. SPA "新对话"
    button, IM "新对话" / "stop" / "新话题" text command).

    For ``surface == SurfaceKind.AUTO_DRIVE`` we always use the
    dedicated ``-auto-`` session (ignore ``explicit_new``) so cron runs
    don't shove their drafts into the user's main scrollback.

    For ``surface == SurfaceKind.SPA_RECORD_COPILOT`` with a
    ``record_ref`` we scope the session to that record
    (``user-{id}-rec-{module}-{record_id}-day-{date}``). Reason:
    hermes persists ``agent_sessions`` by session_id and replays the
    full history into the LLM context on every turn. Without
    per-record scoping, asking about accounts#426 right after
    accounts#427 lets accounts#427's contact name / amounts bleed
    into accounts#426's reply (verified bug: 2026-05-26 SPA copilot
    cross-record contamination — Copilot under '北来顺' wrote tasks
    referencing '西来顺 accounts#427'). The fix is here, not in the
    system prompt, because token-level repetition in conversation
    history outweighs a single system message.

    Phase 5.0 prep (2026-05-09 plan v1.2 §4.1) — ``dept_id`` parameter
    accepted for forward compat. Body does NOT read it. 5.3 will use
    it to derive a ``dept-{id}-day-{date}`` session for the team-tick
    auto_drive run. Until then, every value of ``dept_id`` (including
    explicit ints) produces an identical session id — explicitly
    verified by ``test_phase5_prep_signatures.py``.
    """
    _ = dept_id  # Phase 5.0: accepted but unused (strict 0 behavior change)
    tz = tz or _FALLBACK_TZ
    if surface == SurfaceKind.AUTO_DRIVE:
        return f"user-{int(user_id)}-auto-{_today_in_tz(tz)}"
    if explicit_new:
        return f"user-{int(user_id)}-thread-{uuid.uuid4().hex[:12]}"
    if surface == SurfaceKind.SPA_RECORD_COPILOT and record_ref:
        try:
            mod, rid = record_ref
        except (ValueError, TypeError):
            mod, rid = None, None
        if mod and rid:
            return (
                f"user-{int(user_id)}-rec-{mod}-{int(rid)}"
                f"-day-{_today_in_tz(tz)}"
            )
    return f"user-{int(user_id)}-day-{_today_in_tz(tz)}"


def lookup_user_tz(user_id: int) -> str:
    """Best-effort timezone for ``user_id``.

    Reads ``user_channel_prefs.timezone`` from any of the user's bound
    channels; first-write-wins. Falls back to the plan default
    ``"Asia/Shanghai"`` when the user has no prefs row yet.
    """
    try:
        from hermes.crm.db import db_session
        with db_session() as db:
            row = db.fetchone(
                "SELECT timezone FROM user_channel_prefs "
                " WHERE user_id = %s "
                " ORDER BY updated_at DESC LIMIT 1",
                (int(user_id),),
            )
            if row and row.get("timezone"):
                return str(row["timezone"])
    except Exception:
        pass
    return _FALLBACK_TZ
