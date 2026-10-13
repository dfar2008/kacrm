"""``/api/me/preferences/channel_push`` GET / PUT.

Phase 4 plan v2.2 §4.4 + §10.3 — surface ``user_channel_prefs``
(alembic 0049) to the SPA so users can manage:

  · ``push_today_priority`` / ``push_weekly``  — daily / weekly opt-in
  · ``push_alert_min_sev``                     — high / med / low gate
  · ``quiet_hours_start`` + ``quiet_hours_end`` — TZ-aware quiet window
  · ``timezone``                                — IANA tz name
  · ``max_per_day``                             — push cap
  · ``muted_signals``                           — signal_id allowlist
  · ``prefer_team_brief``                       — Phase 5 hook (already
                                                   in schema, silent today)

The DAO layer (``hermes_cloud.app.channel.store``) was built in Week 1
Day 1; this module is just the HTTP surface plus a ``register_routes``
hook that ``main.py`` calls during app construction.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from hermes.crm.db import DbWrapper, get_db
from hermes_cloud.app.channel.store import (
    ALERT_SEVERITY_ORDER,
    UserChannelPrefs,
    get_or_default_prefs,
    upsert_prefs,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────


# Whitelist of channel keys the SPA can address.  Adding a new surface
# kind here is intentional: each one becomes a separately-configurable
# row in ``user_channel_prefs``.  ``default`` is the safety-net row
# Stage A reads when no per-channel row exists.
ALLOWED_CHANNELS: List[str] = [
    "feishu",
    "ilink",
    "spa_record_copilot",
    "spa_chatbox",
    "default",
]


# Mutable fields. Anything not in this set is rejected on PUT — keeps
# the API a tight contract instead of "send any column you like".
_MUTABLE_FIELDS = {
    "push_today_priority", "push_weekly", "push_alert_min_sev",
    "quiet_hours_start", "quiet_hours_end", "max_per_day",
    "timezone", "muted_signals", "prefer_team_brief",
    "decisions_daily_push_enabled",
}


# ──────────────────────────────────────────────────────────────────────
# Marshalling
# ──────────────────────────────────────────────────────────────────────


def prefs_to_json(prefs: UserChannelPrefs) -> Dict[str, Any]:
    """Render a ``UserChannelPrefs`` as the JSON shape the SPA expects."""
    return {
        "user_id": int(prefs.user_id),
        "channel": str(prefs.channel),
        "push_today_priority": bool(prefs.push_today_priority),
        "push_weekly": bool(prefs.push_weekly),
        "push_alert_min_sev": str(prefs.push_alert_min_sev),
        "quiet_hours_start": int(prefs.quiet_hours_start),
        "quiet_hours_end": int(prefs.quiet_hours_end),
        "max_per_day": int(prefs.max_per_day),
        "timezone": str(prefs.timezone),
        "muted_signals": list(prefs.muted_signals or []),
        "prefer_team_brief": bool(prefs.prefer_team_brief),
        # Tri-state: None means "user hasn't been prompted yet" — the
        # SPA's first-visit modal uses this to decide whether to show.
        "decisions_daily_push_enabled": prefs.decisions_daily_push_enabled,
        "updated_at": (
            prefs.updated_at.isoformat() if prefs.updated_at else None
        ),
    }


def apply_patch(
    prefs: UserChannelPrefs, patch: Dict[str, Any],
) -> UserChannelPrefs:
    """Pure helper: read-modify-write a ``UserChannelPrefs`` from a
    partial JSON patch.  Validates per-field shape; raises ``ValueError``
    on bad values so the route layer can map to 422.

    Returns a NEW dataclass instance (doesn't mutate ``prefs``) — easier
    to reason about and to test.
    """
    # Reject unknown fields first so a typo in the payload is loud.
    unknown = set(patch.keys()) - _MUTABLE_FIELDS - {"channel", "user_id"}
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    out = UserChannelPrefs(
        user_id=prefs.user_id,
        channel=prefs.channel,
        push_today_priority=prefs.push_today_priority,
        push_weekly=prefs.push_weekly,
        push_alert_min_sev=prefs.push_alert_min_sev,
        quiet_hours_start=prefs.quiet_hours_start,
        quiet_hours_end=prefs.quiet_hours_end,
        max_per_day=prefs.max_per_day,
        timezone=prefs.timezone,
        muted_signals=list(prefs.muted_signals or []),
        prefer_team_brief=prefs.prefer_team_brief,
        decisions_daily_push_enabled=prefs.decisions_daily_push_enabled,
        updated_at=prefs.updated_at,
    )

    if "push_today_priority" in patch:
        out.push_today_priority = bool(patch["push_today_priority"])
    if "push_weekly" in patch:
        out.push_weekly = bool(patch["push_weekly"])
    if "prefer_team_brief" in patch:
        out.prefer_team_brief = bool(patch["prefer_team_brief"])
    if "decisions_daily_push_enabled" in patch:
        v = patch["decisions_daily_push_enabled"]
        # Accept None (unprompt → re-show modal), True, or False.
        if v is None or isinstance(v, bool):
            out.decisions_daily_push_enabled = v
        else:
            raise ValueError(
                "decisions_daily_push_enabled must be true / false / null",
            )

    if "push_alert_min_sev" in patch:
        v = str(patch["push_alert_min_sev"])
        if v not in ALERT_SEVERITY_ORDER:
            raise ValueError(
                f"push_alert_min_sev must be one of "
                f"{ALERT_SEVERITY_ORDER}, got {v!r}"
            )
        out.push_alert_min_sev = v

    for hour_field in ("quiet_hours_start", "quiet_hours_end"):
        if hour_field in patch:
            try:
                h = int(patch[hour_field])
            except (TypeError, ValueError):
                raise ValueError(f"{hour_field} must be an int 0-23")
            if not (0 <= h <= 23):
                raise ValueError(f"{hour_field} must be 0-23, got {h}")
            setattr(out, hour_field, h)

    if "max_per_day" in patch:
        try:
            n = int(patch["max_per_day"])
        except (TypeError, ValueError):
            raise ValueError("max_per_day must be a non-negative int")
        if n < 0 or n > 100:
            # 100 is a generous upper bound — stops a broken client from
            # turning a single channel into a spam vector.
            raise ValueError(f"max_per_day must be 0-100, got {n}")
        out.max_per_day = n

    if "timezone" in patch:
        tz = str(patch["timezone"]).strip()
        if not tz:
            raise ValueError("timezone must be non-empty")
        # Validate against the IANA database — catches typos like
        # "Asia/Shangai" before we store them and break the quiet-hours
        # math at push time.
        try:
            import zoneinfo
            zoneinfo.ZoneInfo(tz)
        except Exception:
            raise ValueError(f"timezone {tz!r} is not a valid IANA name")
        out.timezone = tz

    if "muted_signals" in patch:
        raw = patch["muted_signals"]
        if not isinstance(raw, list):
            raise ValueError("muted_signals must be a list of strings")
        cleaned = [str(x) for x in raw if str(x).strip()]
        # 200 is plenty for a per-user per-channel mute list — defensive
        # cap so a buggy SPA can't blow up the row.
        if len(cleaned) > 200:
            raise ValueError("muted_signals capped at 200 entries")
        out.muted_signals = cleaned

    return out


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────


def _user_id_from_request(request: Request, app) -> int:
    """Resolve the calling user's int id from the auth middleware.

    The cloud has two parallel auth surfaces:
      * ``require_bearer_auth`` (legacy /api/me) returns a string id.
      * ``bridge_current_user`` (CRM session cookie) returns a dataclass.

    We accept either — both paths land in the same prefs row keyed
    by ``user_id``.
    """
    # Try bearer first — the SPA settings page mounts under the same
    # authenticated context as /api/me.
    try:
        from hermes_cloud.app.auth import require_bearer_auth
        cfg = app.state.config
        uid = require_bearer_auth(request, cfg)
        if uid:
            return int(uid)
    except Exception:
        pass
    # Fallback: CRM session cookie.
    try:
        from hermes_cloud.app.bridge.identity import current_user as bridge_current_user
        account = bridge_current_user(request)
        return int(account.id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="not authenticated")


def register_routes(app) -> None:
    """Mount GET / PUT ``/api/me/preferences/channel_push`` on ``app``.

    Called from ``main.py`` during the Phase-2 router-mount block, same
    place as ``copilot.routes.register_routes``.
    """
    router = APIRouter(prefix="/api/me/preferences", tags=["preferences"])

    @router.get("/channel_push")
    def get_channel_push_prefs(
        request: Request,
        channel: Optional[str] = None,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        user_id = _user_id_from_request(request, app)

        if channel is not None:
            if channel not in ALLOWED_CHANNELS:
                raise HTTPException(
                    status_code=422,
                    detail=f"channel must be one of {ALLOWED_CHANNELS}",
                )
            prefs = get_or_default_prefs(db, user_id=user_id, channel=channel)
            return {"prefs": prefs_to_json(prefs)}

        # No channel → return all whitelisted channels keyed by name.
        # SPA settings page shows them as separate sub-cards.
        out: Dict[str, Any] = {}
        for ch in ALLOWED_CHANNELS:
            prefs = get_or_default_prefs(db, user_id=user_id, channel=ch)
            out[ch] = prefs_to_json(prefs)
        return {"channels": out}

    @router.put("/channel_push")
    def put_channel_push_prefs(
        payload: Dict[str, Any],
        request: Request,
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        user_id = _user_id_from_request(request, app)

        channel = str(payload.get("channel") or "").strip()
        if not channel:
            raise HTTPException(
                status_code=422, detail="channel is required",
            )
        if channel not in ALLOWED_CHANNELS:
            raise HTTPException(
                status_code=422,
                detail=f"channel must be one of {ALLOWED_CHANNELS}",
            )

        # Read-modify-write so callers can patch one field without
        # losing the others. The DAO upserts with NOW() for updated_at.
        current = get_or_default_prefs(db, user_id=user_id, channel=channel)
        try:
            updated = apply_patch(current, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        try:
            upsert_prefs(db, updated)
        except Exception:
            logger.exception(
                "channel_push_prefs upsert_failed user=%s channel=%s",
                user_id, channel,
            )
            raise HTTPException(
                status_code=500, detail="failed to save preferences",
            )

        # Re-read so the returned shape carries the DB-side updated_at.
        saved = get_or_default_prefs(db, user_id=user_id, channel=channel)
        return {"prefs": prefs_to_json(saved)}

    app.include_router(router)
