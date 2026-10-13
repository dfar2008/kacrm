"""Concurrent cache-miss ``/api/insights/list_brief`` requests for the
same (user, module, signature) must share ONE generation task
(single-flight).

Regression: 2026-07-07 08:17 — opening the SPA fanned out 10 concurrent
utility-crm-daily-brief agent sessions for only 4 module views (right-rail
CopilotListBrief + main-area TodayDecisionPrompts both fetch, dev
StrictMode doubles the effects), each burning a full ~38k-token agent
run to produce an identical brief. ``_list_brief_inner`` spawned a fresh
``_generate_brief_and_cache`` task on every cache miss with no in-flight
dedup.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from hermes_cloud.app.insights import api as insights_api


@contextmanager
def _fake_db_session():
    yield MagicMock()


def _patch_aggregates(monkeypatch):
    """Neutralize every data source ``_list_brief_inner`` touches so the
    test exercises only the cache-miss → generation-task path."""
    monkeypatch.setattr(insights_api, "db_session", _fake_db_session)
    monkeypatch.setattr(
        insights_api, "_build_daily_feed",
        lambda *a, **k: {"outcomes": {}, "counts": {}, "items": []},
    )
    monkeypatch.setattr(
        insights_api, "_build_today_priority", lambda *a, **k: [],
    )
    monkeypatch.setattr(
        insights_api, "_fallback_summary", lambda *a, **k: "fb",
    )
    monkeypatch.setattr(
        insights_api, "_fallback_suggestions", lambda *a, **k: [],
    )
    # These are imported inside the function body — patch at the source.
    import hermes.crm.insights.ai_noticed as ai_noticed_mod
    monkeypatch.setattr(
        ai_noticed_mod, "compute_ai_noticed", lambda *a, **k: [],
    )
    import hermes.crm.insights.brief_cache as brief_cache_mod
    monkeypatch.setattr(
        brief_cache_mod, "get_cached_brief", lambda *a, **k: None,
    )
    import hermes.crm.insights.coach_cards as coach_mod
    monkeypatch.setattr(
        coach_mod, "read_coach_proposals", lambda *a, **k: [],
    )


def _counting_generator(calls, result=lambda: {"summary": "s", "suggestions": []}):
    async def fake_generate(**kwargs):
        calls["n"] += 1
        # Long enough that concurrent requests overlap in-flight.
        await asyncio.sleep(0.05)
        return result()
    return fake_generate


@pytest.mark.asyncio
async def test_concurrent_same_key_shares_one_generation(monkeypatch):
    _patch_aggregates(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(
        insights_api, "_generate_brief_and_cache", _counting_generator(calls),
    )

    r1, r2, r3 = await asyncio.gather(
        insights_api._list_brief_inner(1, "accounts", {"v": ""}, app=None),
        insights_api._list_brief_inner(1, "accounts", {"v": ""}, app=None),
        insights_api._list_brief_inner(1, "accounts", {"v": ""}, app=None),
    )

    assert calls["n"] == 1
    # All requesters still get the generated brief, not the fallback.
    assert r1["summary"] == "s"
    assert r2["summary"] == "s"
    assert r3["summary"] == "s"


@pytest.mark.asyncio
async def test_different_keys_generate_independently(monkeypatch):
    _patch_aggregates(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(
        insights_api, "_generate_brief_and_cache", _counting_generator(calls),
    )

    await asyncio.gather(
        insights_api._list_brief_inner(1, "accounts", {"v": ""}, app=None),
        insights_api._list_brief_inner(1, "leads", {"v": ""}, app=None),
        insights_api._list_brief_inner(2, "accounts", {"v": ""}, app=None),
    )

    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_finished_task_does_not_block_regeneration(monkeypatch):
    """A completed (e.g. failed → None) generation must not poison the
    key: a later request spawns a fresh task instead of joining a dead
    one — and the registry doesn't leak entries."""
    _patch_aggregates(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(
        insights_api,
        "_generate_brief_and_cache",
        _counting_generator(calls, result=lambda: None),
    )

    await insights_api._list_brief_inner(1, "accounts", {"v": ""}, app=None)
    # Let done-callbacks run.
    await asyncio.sleep(0)
    await insights_api._list_brief_inner(1, "accounts", {"v": ""}, app=None)
    await asyncio.sleep(0)

    assert calls["n"] == 2
    assert insights_api._INFLIGHT_GENERATIONS == {}
