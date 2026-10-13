"""DAO around the 4 Phase 4 channel tables.

Each table has its own dataclass + a small set of CRUD helpers. We
deliberately keep this thin — no ORM, just typed wrappers over
``DbWrapper`` (matches the rest of the cloud-side data layer:
``app/insights/question_cache.py``, ``app/copilot/...``, etc.).

Convention:

* All public functions take ``db: DbWrapper`` as the first positional
  argument so callers control the transaction boundary (typically via
  ``with db_session() as db:``).
* Mutations do NOT call ``db._maybe_commit`` themselves; the caller's
  ``with`` block handles commit/rollback.
* Every dataclass has a ``from_row`` classmethod for psycopg
  dict-row → typed conversion.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from hermes.crm.db import DbWrapper

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# user_channel_prefs (alembic 0049)
# ──────────────────────────────────────────────────────────────────────


# Severity ladder — kept here so call sites don't drift.
ALERT_SEVERITY_ORDER: Tuple[str, ...] = ("low", "med", "high")


@dataclass
class UserChannelPrefs:
    """Per-(user, channel) push policy. See ``alembic 0049`` for the
    table; defaults match those in the migration so "default-all" stays
    safe even before the row is materialized."""

    user_id: int
    channel: str
    push_today_priority: bool = True
    push_weekly: bool = True
    push_alert_min_sev: str = "high"
    quiet_hours_start: int = 22
    quiet_hours_end: int = 8
    max_per_day: int = 3
    timezone: str = "Asia/Shanghai"
    muted_signals: List[str] = field(default_factory=list)
    # Phase 5 hook (plan §10.4); Phase 4 doesn't read this column but it
    # exists in the schema and we surface it in the dataclass so future
    # writers don't have to migrate.
    prefer_team_brief: bool = False
    # Decisions feature (alembic 0056 / spec §六·d). Tri-state:
    #   None  — unprompted; SPA shows first-visit modal, backend treats
    #           as opt-IN for managers.
    #   True  — explicit opt-in.
    #   False — explicit opt-out; ``decisions_daily_push`` skips this user.
    decisions_daily_push_enabled: Optional[bool] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "UserChannelPrefs":
        return cls(
            user_id=int(row["user_id"]),
            channel=str(row["channel"]),
            push_today_priority=bool(row["push_today_priority"]),
            push_weekly=bool(row["push_weekly"]),
            push_alert_min_sev=str(row["push_alert_min_sev"]),
            quiet_hours_start=int(row["quiet_hours_start"]),
            quiet_hours_end=int(row["quiet_hours_end"]),
            max_per_day=int(row["max_per_day"]),
            timezone=str(row["timezone"]),
            muted_signals=list(row.get("muted_signals") or []),
            prefer_team_brief=bool(row.get("prefer_team_brief", False)),
            decisions_daily_push_enabled=(
                None if row.get("decisions_daily_push_enabled") is None
                else bool(row.get("decisions_daily_push_enabled"))
            ),
            updated_at=row.get("updated_at"),
        )


def get_prefs(
    db: DbWrapper, *, user_id: int, channel: str,
) -> Optional[UserChannelPrefs]:
    """Return the row for (user, channel) if it exists; None otherwise.

    Callers that want default behavior on missing row should use
    :func:`get_or_default_prefs` instead — that hands back a defaulted
    dataclass without writing.
    """
    row = db.fetchone(
        "SELECT * FROM user_channel_prefs "
        " WHERE user_id = %s AND channel = %s",
        (int(user_id), str(channel)),
    )
    return UserChannelPrefs.from_row(row) if row else None


def get_or_default_prefs(
    db: DbWrapper, *, user_id: int, channel: str,
) -> UserChannelPrefs:
    """Like :func:`get_prefs` but returns a defaulted, unsaved instance
    when the row is missing — handy for read paths that don't want to
    materialize a row just to read it."""
    return (
        get_prefs(db, user_id=user_id, channel=channel)
        or UserChannelPrefs(user_id=int(user_id), channel=str(channel))
    )


def upsert_prefs(
    db: DbWrapper, prefs: UserChannelPrefs,
) -> None:
    """Insert or update a row. Always touches ``updated_at``."""
    db.execute(
        """
        INSERT INTO user_channel_prefs
          (user_id, channel,
           push_today_priority, push_weekly, push_alert_min_sev,
           quiet_hours_start, quiet_hours_end, max_per_day,
           timezone, muted_signals, prefer_team_brief,
           decisions_daily_push_enabled, updated_at)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW())
        ON CONFLICT (user_id, channel) DO UPDATE SET
          push_today_priority           = EXCLUDED.push_today_priority,
          push_weekly                   = EXCLUDED.push_weekly,
          push_alert_min_sev            = EXCLUDED.push_alert_min_sev,
          quiet_hours_start             = EXCLUDED.quiet_hours_start,
          quiet_hours_end               = EXCLUDED.quiet_hours_end,
          max_per_day                   = EXCLUDED.max_per_day,
          timezone                      = EXCLUDED.timezone,
          muted_signals                 = EXCLUDED.muted_signals,
          prefer_team_brief             = EXCLUDED.prefer_team_brief,
          decisions_daily_push_enabled  = EXCLUDED.decisions_daily_push_enabled,
          updated_at                    = NOW()
        """,
        (
            int(prefs.user_id), str(prefs.channel),
            bool(prefs.push_today_priority),
            bool(prefs.push_weekly),
            str(prefs.push_alert_min_sev),
            int(prefs.quiet_hours_start),
            int(prefs.quiet_hours_end),
            int(prefs.max_per_day),
            str(prefs.timezone),
            json.dumps(list(prefs.muted_signals or [])),
            bool(prefs.prefer_team_brief),
            prefs.decisions_daily_push_enabled,  # Optional[bool] passes through
        ),
    )


def is_in_quiet_hours(prefs: UserChannelPrefs, now: Optional[datetime] = None) -> bool:
    """True if ``now`` (in the user's timezone) falls inside the quiet
    window. Window may wrap midnight (default 22→8)."""
    import zoneinfo

    tz = zoneinfo.ZoneInfo(prefs.timezone)
    if now is None:
        now = datetime.now(tz=tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    h = now.hour
    s, e = prefs.quiet_hours_start, prefs.quiet_hours_end
    if s == e:
        return False  # zero-width window = never quiet
    if s < e:
        return s <= h < e
    # wrap (e.g. 22→8): quiet if h>=s OR h<e
    return h >= s or h < e


def alert_severity_meets(
    prefs: UserChannelPrefs, signal_severity: str,
) -> bool:
    """True if a signal with ``signal_severity`` clears the user's
    minimum severity gate. Unknown severities are treated as low."""
    try:
        cap = ALERT_SEVERITY_ORDER.index(prefs.push_alert_min_sev)
    except ValueError:
        cap = ALERT_SEVERITY_ORDER.index("high")  # safe default
    try:
        sev = ALERT_SEVERITY_ORDER.index(signal_severity)
    except ValueError:
        sev = ALERT_SEVERITY_ORDER.index("low")
    return sev >= cap


# ──────────────────────────────────────────────────────────────────────
# channel_push_log (alembic 0050) — sidecar; SoT is interactions
# ──────────────────────────────────────────────────────────────────────


PUSH_KIND_DAILY = "daily_completed"
PUSH_KIND_WEEKLY = "weekly"
PUSH_KIND_ALERT = "alert"
PUSH_KIND_WELCOME = "welcome"
PUSH_KIND_THROTTLED = "throttled"

# Default dedup window for (user, kind, ref_id).
DEDUP_WINDOW = timedelta(hours=24)


@dataclass
class ChannelPushLogEntry:
    id: Optional[int]
    interaction_id: str
    user_id: int
    channel: str
    kind: str
    ref_id: Optional[str]
    rendered: Dict[str, Any]
    pushed_at: Optional[datetime] = None
    delivered: bool = False
    error: Optional[str] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ChannelPushLogEntry":
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            interaction_id=str(row["interaction_id"]),
            user_id=int(row["user_id"]),
            channel=str(row["channel"]),
            kind=str(row["kind"]),
            ref_id=row.get("ref_id"),
            rendered=row.get("rendered") or {},
            pushed_at=row.get("pushed_at"),
            delivered=bool(row.get("delivered", False)),
            error=row.get("error"),
        )


def insert_push_log(
    db: DbWrapper, entry: ChannelPushLogEntry,
) -> int:
    """Insert one row, return its id."""
    row = db.fetchone(
        """
        INSERT INTO channel_push_log
          (interaction_id, user_id, channel, kind, ref_id,
           rendered, delivered, error)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        RETURNING id
        """,
        (
            str(entry.interaction_id),
            int(entry.user_id),
            str(entry.channel),
            str(entry.kind),
            entry.ref_id,
            json.dumps(entry.rendered or {}),
            bool(entry.delivered),
            entry.error,
        ),
    )
    return int(row["id"]) if row else 0


def mark_delivered(
    db: DbWrapper, *, push_log_id: int, error: Optional[str] = None,
) -> None:
    """Update an existing row with the bridge's ack (or failure)."""
    db.execute(
        "UPDATE channel_push_log "
        "   SET delivered = %s, error = %s "
        " WHERE id = %s",
        (error is None, error, int(push_log_id)),
    )


def already_pushed_recently(
    db: DbWrapper, *, user_id: int, kind: str, ref_id: Optional[str],
    window: timedelta = DEDUP_WINDOW,
) -> bool:
    """True if (user, kind, ref_id) has been pushed within ``window``.

    ``ref_id`` may be NULL/None for kinds that don't dedup on a
    business id (welcome, weekly) — in that case we just bound by
    (user, kind, time)."""
    if ref_id is None:
        row = db.fetchone(
            "SELECT 1 FROM channel_push_log "
            " WHERE user_id = %s AND kind = %s "
            "   AND pushed_at > NOW() - %s "
            " LIMIT 1",
            (int(user_id), str(kind), window),
        )
    else:
        row = db.fetchone(
            "SELECT 1 FROM channel_push_log "
            " WHERE user_id = %s AND kind = %s AND ref_id = %s "
            "   AND pushed_at > NOW() - %s "
            " LIMIT 1",
            (int(user_id), str(kind), str(ref_id), window),
        )
    return row is not None


def count_today(
    db: DbWrapper, *, user_id: int, kind: str, tz: str = "Asia/Shanghai",
) -> int:
    """Count pushes of ``kind`` for ``user_id`` today (in their tz).

    The index is on raw ``pushed_at``, so we use a range predicate
    rather than a date cast (PG would refuse the cast in an index
    expression — see migration 0050 NOTE)."""
    row = db.fetchone(
        """
        SELECT COUNT(*) AS n
          FROM channel_push_log
         WHERE user_id = %s
           AND kind = %s
           AND pushed_at >= date_trunc('day', NOW() AT TIME ZONE %s) AT TIME ZONE %s
           AND pushed_at <  date_trunc('day', (NOW() AT TIME ZONE %s) + interval '1 day') AT TIME ZONE %s
        """,
        (int(user_id), str(kind), tz, tz, tz, tz),
    )
    return int(row["n"]) if row else 0


# ──────────────────────────────────────────────────────────────────────
# channel_quick_replies (alembic 0051) — TTL slot mapping
# ──────────────────────────────────────────────────────────────────────


# How long a freshly-rendered quick-reply slot stays valid.
QUICK_REPLY_TTL = timedelta(hours=1)


@dataclass
class QuickReplyPayload:
    """Discriminated union by ``kind``:

    - ``approve`` → execute pending action ``action_id``
    - ``cancel``  → cancel pending action ``action_id``
    - ``copilot_prompt`` → forward ``text`` to the Copilot send path
    - ``mute_signal`` → add ``signal`` to user's muted_signals
    """

    kind: str
    action_id: Optional[str] = None
    text: Optional[str] = None
    signal: Optional[str] = None

    def to_jsonable(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.action_id is not None:
            out["action_id"] = self.action_id
        if self.text is not None:
            out["text"] = self.text
        if self.signal is not None:
            out["signal"] = self.signal
        return out

    @classmethod
    def from_jsonable(cls, raw: Dict[str, Any]) -> "QuickReplyPayload":
        return cls(
            kind=str(raw.get("kind") or ""),
            action_id=raw.get("action_id"),
            text=raw.get("text"),
            signal=raw.get("signal"),
        )


@dataclass
class ChannelQuickReply:
    id: Optional[int]
    user_id: int
    channel: str
    slot: int
    payload: QuickReplyPayload
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ChannelQuickReply":
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            user_id=int(row["user_id"]),
            channel=str(row["channel"]),
            slot=int(row["slot"]),
            payload=QuickReplyPayload.from_jsonable(row.get("payload") or {}),
            created_at=row.get("created_at"),
            expires_at=row.get("expires_at"),
            consumed_at=row.get("consumed_at"),
        )


def insert_quick_replies(
    db: DbWrapper, *, user_id: int, channel: str,
    slots: List[Tuple[int, QuickReplyPayload]],
    ttl: timedelta = QUICK_REPLY_TTL,
) -> List[int]:
    """Bulk-insert N quick-reply slots for a single push. Returns the
    inserted row ids in input order. Existing live slots (same user +
    channel + slot) are NOT auto-consumed — the next push's slots
    override by being more recent in the lookup index, which orders
    by ``expires_at`` desc."""
    if not slots:
        return []
    expires_at = datetime.now(tz=timezone.utc) + ttl
    ids: List[int] = []
    for slot, payload in slots:
        row = db.fetchone(
            """
            INSERT INTO channel_quick_replies
              (user_id, channel, slot, payload, expires_at)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                int(user_id), str(channel), int(slot),
                json.dumps(payload.to_jsonable()),
                expires_at,
            ),
        )
        ids.append(int(row["id"]) if row else 0)
    return ids


def consume_quick_reply(
    db: DbWrapper, *, user_id: int, channel: str, slot: int,
) -> Optional[QuickReplyPayload]:
    """Atomically claim the most recent unconsumed, unexpired slot
    matching (user, channel, slot). Returns the payload, or None if no
    matching slot exists. Subsequent calls with the same args return
    None — the slot is single-use."""
    row = db.fetchone(
        """
        UPDATE channel_quick_replies
           SET consumed_at = NOW()
         WHERE id = (
             SELECT id FROM channel_quick_replies
              WHERE user_id = %s AND channel = %s AND slot = %s
                AND consumed_at IS NULL
                AND expires_at > NOW()
              ORDER BY expires_at DESC
              LIMIT 1
              FOR UPDATE SKIP LOCKED
         )
         RETURNING payload
        """,
        (int(user_id), str(channel), int(slot)),
    )
    if not row:
        return None
    return QuickReplyPayload.from_jsonable(row.get("payload") or {})


def prune_expired_quick_replies(
    db: DbWrapper, *, older_than: timedelta = timedelta(days=7),
) -> int:
    """Garbage-collect long-expired or long-consumed rows. Run on a
    weekly cron. Returns the count deleted."""
    row = db.fetchone(
        """
        WITH deleted AS (
            DELETE FROM channel_quick_replies
             WHERE (consumed_at IS NOT NULL AND consumed_at < NOW() - %s)
                OR (expires_at  < NOW() - %s)
            RETURNING 1
        )
        SELECT COUNT(*) AS n FROM deleted
        """,
        (older_than, older_than),
    )
    return int(row["n"]) if row else 0


__all__ = [
    # user_channel_prefs
    "UserChannelPrefs",
    "ALERT_SEVERITY_ORDER",
    "get_prefs",
    "get_or_default_prefs",
    "upsert_prefs",
    "is_in_quiet_hours",
    "alert_severity_meets",
    # channel_push_log
    "ChannelPushLogEntry",
    "PUSH_KIND_DAILY",
    "PUSH_KIND_WEEKLY",
    "PUSH_KIND_ALERT",
    "PUSH_KIND_WELCOME",
    "PUSH_KIND_THROTTLED",
    "DEDUP_WINDOW",
    "insert_push_log",
    "mark_delivered",
    "already_pushed_recently",
    "count_today",
    # channel_quick_replies
    "QuickReplyPayload",
    "ChannelQuickReply",
    "QUICK_REPLY_TTL",
    "insert_quick_replies",
    "consume_quick_reply",
    "prune_expired_quick_replies",]
