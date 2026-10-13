"""Pure-function tests for :mod:`hermes_cloud.app.channel.store`.

DB-touching helpers are covered separately via integration tests
against a real PG instance. This file covers only the helpers that do
not require a live connection:

* ``UserChannelPrefs.from_row`` — dict-row → dataclass conversion.
* ``is_in_quiet_hours`` — wraparound time math, including TZ handling.
* ``alert_severity_meets`` — severity-ladder comparison + safe defaults.
* ``QuickReplyPayload.to_jsonable`` / ``from_jsonable`` — round-trip.
* ``ChannelPushLogEntry.from_row``, ``ChannelQuickReply.from_row``
  — dict-row → dataclass conversion.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hermes_cloud.app.channel.store import (
    ChannelPushLogEntry,
    ChannelQuickReply,
    QuickReplyPayload,
    UserChannelPrefs,
    alert_severity_meets,
    is_in_quiet_hours,
)


# ──────────────────────────────────────────────────────────────────────
# UserChannelPrefs.from_row
# ──────────────────────────────────────────────────────────────────────


def test_user_channel_prefs_from_row_full_payload():
    row = {
        "user_id": 7,
        "channel": "feishu",
        "push_today_priority": True,
        "push_weekly": False,
        "push_alert_min_sev": "med",
        "quiet_hours_start": 23,
        "quiet_hours_end": 7,
        "max_per_day": 5,
        "timezone": "America/Los_Angeles",
        "muted_signals": ["industry_field_change_repeated", "stale_quote"],
        "prefer_team_brief": True,
        "updated_at": datetime(2026, 5, 9, 0, tzinfo=timezone.utc),
    }
    prefs = UserChannelPrefs.from_row(row)
    assert prefs.user_id == 7
    assert prefs.channel == "feishu"
    assert prefs.push_today_priority is True
    assert prefs.push_weekly is False
    assert prefs.push_alert_min_sev == "med"
    assert prefs.quiet_hours_start == 23
    assert prefs.quiet_hours_end == 7
    assert prefs.max_per_day == 5
    assert prefs.timezone == "America/Los_Angeles"
    assert prefs.muted_signals == ["industry_field_change_repeated", "stale_quote"]
    assert prefs.prefer_team_brief is True
    assert prefs.updated_at is not None


def test_user_channel_prefs_from_row_missing_optional():
    """``muted_signals`` may be missing; ``prefer_team_brief`` defaults
    to False; ``updated_at`` may be NULL."""
    row = {
        "user_id": 1,
        "channel": "wecom_ilink",
        "push_today_priority": True,
        "push_weekly": True,
        "push_alert_min_sev": "high",
        "quiet_hours_start": 22,
        "quiet_hours_end": 8,
        "max_per_day": 3,
        "timezone": "Asia/Shanghai",
        # muted_signals + prefer_team_brief + updated_at omitted
    }
    prefs = UserChannelPrefs.from_row(row)
    assert prefs.muted_signals == []
    assert prefs.prefer_team_brief is False
    assert prefs.updated_at is None


# ──────────────────────────────────────────────────────────────────────
# is_in_quiet_hours
# ──────────────────────────────────────────────────────────────────────


def _prefs(start: int, end: int, tz: str = "Asia/Shanghai") -> UserChannelPrefs:
    return UserChannelPrefs(
        user_id=1, channel="feishu",
        quiet_hours_start=start, quiet_hours_end=end,
        timezone=tz,
    )


@pytest.mark.parametrize("hour, expected", [
    (21, False),  # 21:00 — just before quiet
    (22, True),   # 22:00 — start of quiet
    (23, True),
    (0, True),    # midnight wraparound
    (3, True),
    (7, True),    # 07:59 still quiet (end is exclusive)
    (8, False),   # 08:00 — quiet ends (inclusive end)
    (12, False),
])
def test_quiet_hours_default_window_22_to_8(hour: int, expected: bool):
    """Default window 22→8 wraps midnight."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 5, 9, hour, 0, 0, tzinfo=tz)
    assert is_in_quiet_hours(_prefs(22, 8), now=now) is expected


@pytest.mark.parametrize("hour, expected", [
    (8, False),
    (9, True),
    (11, True),
    (12, False),
])
def test_quiet_hours_non_wrap_window_9_to_12(hour: int, expected: bool):
    """A non-wrap window 9→12 = quiet from 09:00 to 11:59."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 5, 9, hour, 0, 0, tzinfo=tz)
    assert is_in_quiet_hours(_prefs(9, 12), now=now) is expected


def test_quiet_hours_zero_width_window_never_quiet():
    """``start == end`` is treated as zero-width = never quiet."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    for hour in range(0, 24):
        now = datetime(2026, 5, 9, hour, 0, 0, tzinfo=tz)
        assert is_in_quiet_hours(_prefs(10, 10), now=now) is False


def test_quiet_hours_naive_now_assumed_in_user_tz():
    """A naive datetime is interpreted in the user's timezone."""
    naive_in_tz = datetime(2026, 5, 9, 23, 0, 0)  # 23:00 in user tz
    assert is_in_quiet_hours(_prefs(22, 8), now=naive_in_tz) is True


# ──────────────────────────────────────────────────────────────────────
# alert_severity_meets
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("min_sev, signal, expected", [
    ("low", "low", True),
    ("low", "med", True),
    ("low", "high", True),
    ("med", "low", False),
    ("med", "med", True),
    ("med", "high", True),
    ("high", "low", False),
    ("high", "med", False),
    ("high", "high", True),
])
def test_alert_severity_meets(min_sev: str, signal: str, expected: bool):
    prefs = UserChannelPrefs(user_id=1, channel="feishu", push_alert_min_sev=min_sev)
    assert alert_severity_meets(prefs, signal) is expected


def test_alert_severity_unknown_min_treated_as_high():
    """Garbled prefs default to the safest gate (high)."""
    prefs = UserChannelPrefs(user_id=1, channel="feishu", push_alert_min_sev="???")
    # a mid-severity signal should be suppressed under the safe default
    assert alert_severity_meets(prefs, "med") is False
    # only 'high' clears the safe default
    assert alert_severity_meets(prefs, "high") is True


def test_alert_severity_unknown_signal_treated_as_low():
    """Garbled signal severity treated as 'low' so it can't sneak past."""
    prefs = UserChannelPrefs(user_id=1, channel="feishu", push_alert_min_sev="med")
    assert alert_severity_meets(prefs, "???") is False


# ──────────────────────────────────────────────────────────────────────
# QuickReplyPayload round-trip
# ──────────────────────────────────────────────────────────────────────


def test_quick_reply_payload_approve_round_trip():
    p = QuickReplyPayload(kind="approve", action_id="abc12345")
    raw = p.to_jsonable()
    assert raw == {"kind": "approve", "action_id": "abc12345"}
    assert QuickReplyPayload.from_jsonable(raw) == p


def test_quick_reply_payload_copilot_prompt_round_trip():
    p = QuickReplyPayload(kind="copilot_prompt", text="起一封跟进邮件")
    assert QuickReplyPayload.from_jsonable(p.to_jsonable()) == p


def test_quick_reply_payload_mute_signal_round_trip():
    p = QuickReplyPayload(kind="mute_signal", signal="industry_change_repeated")
    assert QuickReplyPayload.from_jsonable(p.to_jsonable()) == p


def test_quick_reply_payload_to_jsonable_omits_none_fields():
    """Don't bloat the JSONB column with explicit nulls."""
    p = QuickReplyPayload(kind="approve", action_id="x")
    raw = p.to_jsonable()
    assert "text" not in raw
    assert "signal" not in raw


# ──────────────────────────────────────────────────────────────────────
# from_row coverage for the other dataclasses
# ──────────────────────────────────────────────────────────────────────


def test_channel_push_log_entry_from_row():
    row = {
        "id": 42,
        "interaction_id": "11111111-2222-3333-4444-555555555555",
        "user_id": 7,
        "channel": "feishu",
        "kind": "daily_completed",
        "ref_id": "potential-204",
        "rendered": {"card": {"title": "..."}},
        "pushed_at": datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc),
        "delivered": True,
    }
    entry = ChannelPushLogEntry.from_row(row)
    assert entry.id == 42
    assert entry.user_id == 7
    assert entry.kind == "daily_completed"
    assert entry.delivered is True
    assert entry.rendered == {"card": {"title": "..."}}


def test_channel_quick_reply_from_row():
    row = {
        "id": 99,
        "user_id": 7,
        "channel": "wecom_ilink",
        "slot": 2,
        "payload": {"kind": "cancel", "action_id": "xyz"},
        "created_at": datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
    }
    qr = ChannelQuickReply.from_row(row)
    assert qr.slot == 2
    assert qr.payload.kind == "cancel"
    assert qr.payload.action_id == "xyz"


# AutoDriveRun.from_row tests removed — auto_drive engine deleted in
# consolidation Phase 0 (2026-05-16). See
# docs/2026-05-16-decisions-coach-autodrive-consolidation.md.
