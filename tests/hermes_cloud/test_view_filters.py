"""Unit tests for dynamic view-filter resolution (no DB needed)."""
from __future__ import annotations

from datetime import datetime, timezone

import hermes_cloud.app.crm.view_filters as vf


USER = {"id": 42}
FIXED_NOW = datetime(2026, 6, 16, 15, 30, 0, tzinfo=timezone.utc)  # a Tuesday


def test_current_resolves_to_user_id():
    out = vf.resolve_dynamic_filters(
        None,
        [{"field": "owner_by", "operator": "eq", "value": "current"}],
        user=USER,
        now=FIXED_NOW,
    )
    assert out == [{"field": "owner_by", "operator": "eq", "value": 42}]


def test_team_resolves_to_in_list(monkeypatch):
    monkeypatch.setattr(vf, "viewable_user_ids", lambda db, *, caller_id, scope: {42, 7, 9})
    out = vf.resolve_dynamic_filters(
        None,
        [{"field": "owner_by", "operator": "eq", "value": "team"}],
        user=USER,
        now=FIXED_NOW,
    )
    assert out == [{"field": "owner_by", "operator": "in", "value": [7, 9, 42]}]


def test_last_n_days_expands_to_gte_lte_pair():
    out = vf.resolve_dynamic_filters(
        None,
        [{"field": "created_at", "operator": "eq", "value": "last_n_days:7"}],
        user=USER,
        now=FIXED_NOW,
    )
    assert len(out) == 2
    assert out[0]["operator"] == "gte" and out[0]["value"] == "2026-06-09 15:30:00"
    assert out[1]["operator"] == "lte" and out[1]["value"] == "2026-06-16 15:30:00"
    assert all(c["field"] == "created_at" for c in out)


def test_today_expands_to_day_bounds():
    out = vf.resolve_dynamic_filters(
        None,
        [{"field": "latest_contact_date", "operator": "eq", "value": "today"}],
        user=USER,
        now=FIXED_NOW,
    )
    assert [c["operator"] for c in out] == ["gte", "lte"]
    assert out[0]["value"] == "2026-06-16 00:00:00"
    assert out[1]["value"] == "2026-06-16 23:59:59"


def test_this_week_starts_monday():
    out = vf.resolve_dynamic_filters(
        None,
        [{"field": "created_at", "operator": "eq", "value": "this_week"}],
        user=USER,
        now=FIXED_NOW,
    )
    # FIXED_NOW is Tue 2026-06-16 → week starts Mon 2026-06-15.
    assert out[0]["value"] == "2026-06-15 00:00:00"


def test_literal_value_passes_through():
    out = vf.resolve_dynamic_filters(
        None,
        [{"field": "industry", "operator": "eq", "value": "物流供应链"}],
        user=USER,
        now=FIXED_NOW,
    )
    assert out == [{"field": "industry", "operator": "eq", "value": "物流供应链"}]
