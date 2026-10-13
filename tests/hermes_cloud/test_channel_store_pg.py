"""Integration tests for :mod:`hermes_cloud.app.channel.store` against PG.

Requires a real PostgreSQL with the alembic schema at head ≥ 0052.

Skip-conditions: import path of ``hermes.crm.db`` actually resolves +
``HERMES_CRM_DATABASE_URL`` is reachable. Otherwise tests skip — they
don't drag CI down without PG (the conftest stubs psycopg).

Each test isolates its own user_id range to avoid cross-test pollution.
We use ``user_id=99100..99199`` for this file.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest


def _have_real_pg() -> bool:
    """True iff we have a real psycopg + a reachable DSN."""
    try:
        import psycopg  # noqa: F401
    except Exception:
        return False
    if not os.environ.get("HERMES_CRM_DATABASE_URL"):
        return False
    # Don't trip the conftest's stubbed psycopg (which is sys.modules-injected
    # before any real psycopg can load).
    psycopg_mod = sys.modules.get("psycopg")
    if psycopg_mod is None or hasattr(psycopg_mod, "_mock_name"):
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _have_real_pg(),
    reason="real PostgreSQL + HERMES_CRM_DATABASE_URL required",
)


# Imports are deferred to test-time so the skip predicate runs first.


@pytest.fixture
def db_session():
    """Yields a real ``DbWrapper`` session, rolled back after the test."""
    from hermes.crm.db import db_session as _ds
    with _ds() as db:
        yield db
        # auto-commit on context exit; tests should clean themselves up


@pytest.fixture
def fresh_user(db_session):
    """Insert a throwaway user, return its id; clean up after.

    Uses high numeric IDs so we don't collide with seeded data."""
    db = db_session
    row = db.fetchone(
        "INSERT INTO users (username, password, realname) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"channel_store_test_{os.getpid()}", "x", "channel test"),
    )
    user_id = int(row["id"])
    yield user_id
    # Cleanup — cascades to user_channel_prefs / channel_push_log / etc.
    db.execute("DELETE FROM users WHERE id = %s", (user_id,))


# ──────────────────────────────────────────────────────────────────────
# user_channel_prefs round-trip
# ──────────────────────────────────────────────────────────────────────


def test_upsert_then_get_prefs(db_session, fresh_user):
    from hermes_cloud.app.channel.store import (
        UserChannelPrefs, get_prefs, upsert_prefs,
    )
    db, user_id = db_session, fresh_user
    prefs = UserChannelPrefs(
        user_id=user_id, channel="feishu",
        push_alert_min_sev="med", quiet_hours_start=23, quiet_hours_end=7,
        max_per_day=5,
        muted_signals=["industry_field_change_repeated"],
        prefer_team_brief=True,
    )
    upsert_prefs(db, prefs)

    fetched = get_prefs(db, user_id=user_id, channel="feishu")
    assert fetched is not None
    assert fetched.push_alert_min_sev == "med"
    assert fetched.quiet_hours_start == 23
    assert fetched.max_per_day == 5
    assert fetched.muted_signals == ["industry_field_change_repeated"]
    assert fetched.prefer_team_brief is True


def test_upsert_replaces_on_conflict(db_session, fresh_user):
    from hermes_cloud.app.channel.store import (
        UserChannelPrefs, get_prefs, upsert_prefs,
    )
    db, user_id = db_session, fresh_user
    upsert_prefs(db, UserChannelPrefs(user_id=user_id, channel="feishu"))
    upsert_prefs(db, UserChannelPrefs(
        user_id=user_id, channel="feishu",
        push_today_priority=False, max_per_day=99,
    ))
    fetched = get_prefs(db, user_id=user_id, channel="feishu")
    assert fetched.push_today_priority is False
    assert fetched.max_per_day == 99


def test_get_or_default_prefs_returns_defaulted_when_missing(db_session, fresh_user):
    from hermes_cloud.app.channel.store import get_or_default_prefs
    prefs = get_or_default_prefs(
        db_session, user_id=fresh_user, channel="feishu",
    )
    # No row yet — defaults from the dataclass kick in.
    assert prefs.push_today_priority is True
    assert prefs.max_per_day == 3
    assert prefs.timezone == "Asia/Shanghai"


# ──────────────────────────────────────────────────────────────────────
# channel_push_log dedup + count_today
# ──────────────────────────────────────────────────────────────────────


def _emit_dummy_interaction(db) -> str:
    """Insert a minimal kind='channel.push' interaction so push_log's FK
    is satisfied, return its id."""
    row = db.fetchone(
        """
        INSERT INTO interactions (kind, content, source)
        VALUES ('channel.push', '{}'::jsonb, 'test')
        RETURNING id
        """,
    )
    return str(row["id"])


def test_insert_push_log_and_dedup(db_session, fresh_user):
    from hermes_cloud.app.channel.store import (
        ChannelPushLogEntry, PUSH_KIND_ALERT,
        already_pushed_recently, insert_push_log,
    )
    db, user_id = db_session, fresh_user
    iid = _emit_dummy_interaction(db)

    # Initially nothing pushed.
    assert already_pushed_recently(
        db, user_id=user_id, kind=PUSH_KIND_ALERT, ref_id="signal-1",
    ) is False

    insert_push_log(db, ChannelPushLogEntry(
        id=None, interaction_id=iid, user_id=user_id, channel="feishu",
        kind=PUSH_KIND_ALERT, ref_id="signal-1",
        rendered={"text": "..."},
    ))

    # Now dedup hits.
    assert already_pushed_recently(
        db, user_id=user_id, kind=PUSH_KIND_ALERT, ref_id="signal-1",
    ) is True

    # Different ref_id is not deduped.
    assert already_pushed_recently(
        db, user_id=user_id, kind=PUSH_KIND_ALERT, ref_id="signal-2",
    ) is False


def test_count_today_isolates_per_user_per_kind(db_session, fresh_user):
    from hermes_cloud.app.channel.store import (
        ChannelPushLogEntry, PUSH_KIND_ALERT, PUSH_KIND_DAILY,
        count_today, insert_push_log,
    )
    db, user_id = db_session, fresh_user
    iid = _emit_dummy_interaction(db)

    for n in range(3):
        insert_push_log(db, ChannelPushLogEntry(
            id=None, interaction_id=iid, user_id=user_id, channel="feishu",
            kind=PUSH_KIND_ALERT, ref_id=f"signal-{n}",
            rendered={"text": f"alert {n}"},
        ))
    insert_push_log(db, ChannelPushLogEntry(
        id=None, interaction_id=iid, user_id=user_id, channel="feishu",
        kind=PUSH_KIND_DAILY, ref_id="2026-05-09",
        rendered={"card": "morning"},
    ))

    assert count_today(db, user_id=user_id, kind=PUSH_KIND_ALERT) == 3
    assert count_today(db, user_id=user_id, kind=PUSH_KIND_DAILY) == 1
    # Other user's count is independent.
    assert count_today(db, user_id=user_id + 999_999, kind=PUSH_KIND_ALERT) == 0


# ──────────────────────────────────────────────────────────────────────
# channel_quick_replies — single-use atomic claim
# ──────────────────────────────────────────────────────────────────────


def test_quick_reply_consume_is_single_use(db_session, fresh_user):
    from hermes_cloud.app.channel.store import (
        QuickReplyPayload, consume_quick_reply, insert_quick_replies,
    )
    db, user_id = db_session, fresh_user

    insert_quick_replies(
        db, user_id=user_id, channel="feishu",
        slots=[
            (1, QuickReplyPayload(kind="approve", action_id="abc12345")),
            (2, QuickReplyPayload(kind="cancel", action_id="abc12345")),
        ],
    )

    # First consume hits.
    p1 = consume_quick_reply(db, user_id=user_id, channel="feishu", slot=1)
    assert p1 is not None
    assert p1.kind == "approve"
    assert p1.action_id == "abc12345"

    # Second consume of the same slot returns None — single-use.
    p1_again = consume_quick_reply(db, user_id=user_id, channel="feishu", slot=1)
    assert p1_again is None

    # Slot 2 still unconsumed.
    p2 = consume_quick_reply(db, user_id=user_id, channel="feishu", slot=2)
    assert p2 is not None and p2.kind == "cancel"


def test_quick_reply_expired_slot_not_returned(db_session, fresh_user):
    """Manually backdate a slot's expires_at; consume should miss it."""
    from hermes_cloud.app.channel.store import (
        QuickReplyPayload, consume_quick_reply, insert_quick_replies,
    )
    db, user_id = db_session, fresh_user
    insert_quick_replies(
        db, user_id=user_id, channel="feishu",
        slots=[(1, QuickReplyPayload(kind="approve", action_id="x"))],
    )
    # Expire it.
    db.execute(
        "UPDATE channel_quick_replies "
        "   SET expires_at = NOW() - interval '1 hour' "
        " WHERE user_id = %s",
        (user_id,),
    )
    p = consume_quick_reply(db, user_id=user_id, channel="feishu", slot=1)
    assert p is None


# auto_drive_runs lifecycle tests removed — the auto_drive engine was
# deleted in consolidation Phase 0 (2026-05-16). See
# docs/2026-05-16-decisions-coach-autodrive-consolidation.md and
# alembic 0064. Historical interactions(kind='auto_drive.*') rows
# are still preserved as immutable SoT; only the cron-state table
# auto_drive_runs and its lifecycle helpers are gone.
