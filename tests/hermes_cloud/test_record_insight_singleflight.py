"""Concurrent cache-miss ``/api/insights/record`` requests for the same
(module, record_id, signature) must share ONE generation task
(single-flight).

Same defect class as the list_brief dedup (see
test_list_brief_singleflight.py): ``_record_insight_inner`` spawned a
fresh ``_generate_and_cache`` task on every cache miss with no in-flight
dedup, so dev StrictMode double-fires and rapid back-and-forth
navigation to the same record each burned a duplicate LLM run for an
identical result.
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


def _fake_scanner(db, record_id, user_id):
    return {
        "has_data": True,
        "module": "accounts",
        "record_id": record_id,
        "record_name": f"acct-{record_id}",
        "summary": "s",
        "hints": [],
        "suggested_questions": [{"text": "template?"}],
    }


def _patch_sources(monkeypatch):
    """Neutralize every data source ``_record_insight_inner`` touches so
    the test exercises only the cache-miss → generation-task path."""
    monkeypatch.setattr(insights_api, "db_session", _fake_db_session)
    monkeypatch.setattr(
        insights_api, "_recent_interactions_for_record", lambda *a, **k: [],
    )
    monkeypatch.setattr(
        insights_api, "_resolve_record_name", lambda *a, **k: "acct",
    )
    # These are imported inside the function body — patch at the source.
    import hermes.crm.insights.question_cache as qc_mod
    monkeypatch.setattr(
        qc_mod, "compute_signature",
        lambda *, record_name, hint_signals, last_interaction_id:
            f"sig:{record_name}",
    )
    monkeypatch.setattr(qc_mod, "get_cached_questions", lambda *a, **k: None)
    monkeypatch.setattr(qc_mod, "latest_interaction_id", lambda *a, **k: None)


def _counting_generator(calls, result=lambda: [{"text": "llm?"}]):
    async def fake_generate(**kwargs):
        calls["n"] += 1
        # Long enough that concurrent requests overlap in-flight.
        await asyncio.sleep(0.05)
        return result()
    return fake_generate


def _call(module="accounts", record_id=1, user_id=1):
    return insights_api._record_insight_inner(
        request=MagicMock(), module=module, id=record_id,
        scanner=_fake_scanner, user_id=user_id,
        src_holder={"v": ""},
    )


@pytest.mark.asyncio
async def test_concurrent_same_record_shares_one_generation(monkeypatch):
    _patch_sources(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(
        insights_api, "_generate_and_cache", _counting_generator(calls),
    )

    r1, r2, r3 = await asyncio.gather(_call(), _call(), _call())

    assert calls["n"] == 1
    # All requesters still get the generated questions, not the template.
    for r in (r1, r2, r3):
        assert r["suggested_questions"] == [{"text": "llm?"}]
        assert r["question_source"] == "llm_fresh"


@pytest.mark.asyncio
async def test_different_records_generate_independently(monkeypatch):
    _patch_sources(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(
        insights_api, "_generate_and_cache", _counting_generator(calls),
    )

    await asyncio.gather(
        _call(record_id=1),
        _call(record_id=2),
        _call(module="leads", record_id=1),
    )

    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_finished_task_does_not_block_regeneration(monkeypatch):
    """A completed (e.g. failed → []) generation must not poison the
    key: a later request spawns a fresh task instead of joining a dead
    one — and the in-flight registry doesn't leak entries."""
    _patch_sources(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(
        insights_api,
        "_generate_and_cache",
        _counting_generator(calls, result=lambda: []),
    )

    await _call()
    # Let done-callbacks run.
    await asyncio.sleep(0)
    await _call()
    await asyncio.sleep(0)

    assert calls["n"] == 2
    assert insights_api._INFLIGHT_GENERATIONS == {}
