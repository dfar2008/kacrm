"""Unit tests for :mod:`hermes_cloud.app.gateway.record_state` (Charter §A2).

Covers:
  * begin_write → commit_write clears the lock
  * begin_write → rollback_write clears the lock
  * concurrent different agents collide
  * concurrent same agent allowed (independent tokens)
  * writes_since reports other agents' committed writes
  * writes_since excludes the caller's own writes
  * commit / rollback are idempotent
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from _db_url import resolve_database_url

from hermes.action_gateway.record_state import (
    WriteCollisionError,
    begin_write,
    commit_write,
    rollback_write,
    writes_since,
)


@pytest.fixture(scope="module")
def engine():
    url = resolve_database_url()
    if not url:
        pytest.skip("no CRM database URL configured")
    try:
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect():
            pass
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"CRM database not reachable: {exc}")
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def clean_record_writes(engine):
    """Clear record_writes before & after each test."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM record_writes"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM record_writes"))


def test_begin_then_commit_clears_lock(engine, clean_record_writes):
    t = begin_write(
        module="leads", record_id=1,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    commit_write(t, engine=engine)
    # Different agent can now acquire
    t2 = begin_write(
        module="leads", record_id=1,
        agent_name="research", profile_id="42",
        engine=engine,
    )
    assert t2 != t


def test_begin_then_rollback_clears_lock(engine, clean_record_writes):
    t = begin_write(
        module="leads", record_id=2,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    rollback_write(t, engine=engine)
    t2 = begin_write(
        module="leads", record_id=2,
        agent_name="research", profile_id="42",
        engine=engine,
    )
    assert t2 != t


def test_concurrent_different_agents_collide(engine, clean_record_writes):
    begin_write(
        module="leads", record_id=3,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    with pytest.raises(WriteCollisionError) as exc:
        begin_write(
            module="leads", record_id=3,
            agent_name="research", profile_id="42",
            engine=engine,
        )
    msg = str(exc.value)
    assert "leads:3" in msg
    assert "intake" in msg
    assert "research" in msg


def test_concurrent_same_agent_allowed(engine, clean_record_writes):
    t1 = begin_write(
        module="leads", record_id=4,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    t2 = begin_write(
        module="leads", record_id=4,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    assert t1 != t2


def test_writes_since_returns_other_agents_committed_writes(
    engine, clean_record_writes,
):
    """research begins; intake then writes+commits AFTER that begin.
    writes_since should report intake's commit so research can re-fetch
    before its own commit lands.
    """
    research_t = begin_write(
        module="leads", record_id=5,
        agent_name="research", profile_id="42",
        engine=engine,
    )
    # Intake takes a separate path — collides on hard lock, so simulate
    # intake's write happening *between* research's begin and commit by
    # rolling research back, doing intake, and re-using the original
    # research_t row's began_at as the soft-warning anchor. Easier:
    # release research first so intake doesn't collide, then re-acquire.
    rollback_write(research_t, engine=engine)

    research_t = begin_write(
        module="leads", record_id=5,
        agent_name="research", profile_id="42",
        engine=engine,
    )
    rollback_write(research_t, engine=engine)
    # Now intake writes with a began_at AFTER research_t's
    intake_t = begin_write(
        module="leads", record_id=5,
        agent_name="intake", profile_id="42",
        fields={"stage": "won"},
        engine=engine,
    )
    commit_write(intake_t, engine=engine)
    rows = writes_since(
        module="leads", record_id=5,
        since_token=research_t, engine=engine,
    )
    assert len(rows) == 1
    assert rows[0]["agent_name"] == "intake"
    assert rows[0]["fields"] == {"stage": "won"}


def test_writes_since_excludes_own_writes(engine, clean_record_writes):
    anchor = begin_write(
        module="opportunities", record_id=10,
        agent_name="research", profile_id="42",
        engine=engine,
    )
    later_self = begin_write(
        module="opportunities", record_id=10,
        agent_name="research", profile_id="42",
        engine=engine,
    )
    commit_write(later_self, engine=engine)
    rows = writes_since(
        module="opportunities", record_id=10,
        since_token=anchor, engine=engine,
    )
    assert rows == []


def test_writes_since_returns_empty_for_unknown_token(
    engine, clean_record_writes,
):
    rows = writes_since(
        module="leads", record_id=999,
        since_token=999_999_999, engine=engine,
    )
    assert rows == []


def test_commit_is_idempotent(engine, clean_record_writes):
    t = begin_write(
        module="leads", record_id=20,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    commit_write(t, engine=engine)
    # Second call is a silent no-op (UPDATE matches 0 rows)
    commit_write(t, engine=engine)


def test_rollback_is_idempotent(engine, clean_record_writes):
    t = begin_write(
        module="leads", record_id=21,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    rollback_write(t, engine=engine)
    rollback_write(t, engine=engine)


def test_commit_after_rollback_is_noop(engine, clean_record_writes):
    """Rolled-back tokens cannot be committed (and vice versa)."""
    t = begin_write(
        module="leads", record_id=22,
        agent_name="intake", profile_id="42",
        engine=engine,
    )
    rollback_write(t, engine=engine)
    commit_write(t, engine=engine)  # no-op
    # Verify lock is still cleared
    t2 = begin_write(
        module="leads", record_id=22,
        agent_name="research", profile_id="42",
        engine=engine,
    )
    assert t2 != t
