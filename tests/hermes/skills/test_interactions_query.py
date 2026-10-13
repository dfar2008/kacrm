"""Test recent_interactions helper used by analyze_team."""
import datetime as dt
import json
import os
import pytest

# These tests need a real PG connection; auto-skip otherwise.
pytestmark = pytest.mark.skipif(
    os.environ.get("HERMES_TESTS_REAL_PG") != "1",
    reason="requires real PG (set HERMES_TESTS_REAL_PG=1)"
)


def _insert(db, *, actor_id, kind, source="test", at=None, content=None):
    db.execute(
        """INSERT INTO interactions (kind, source, actors, subjects, content, occurred_at)
           VALUES (%s, %s, %s::jsonb, '[]'::jsonb, %s::jsonb, %s)""",
        (kind, source, json.dumps([{"type": "user", "id": actor_id}]),
         json.dumps(content or {}),
         at or dt.datetime.utcnow()),
    )


def _to_naive(dt_obj):
    """Convert timezone-aware datetime to naive UTC."""
    if dt_obj is None:
        return None
    if hasattr(dt_obj, 'replace'):
        return dt_obj.replace(tzinfo=None)
    return dt_obj


@pytest.fixture
def db_session():
    from hermes.crm.db import db_session as _ds
    with _ds() as db:
        yield db


def test_recent_interactions_filters_by_actor(db_session):
    from hermes.skills._utils.interactions_query import recent_interactions
    db = db_session
    _insert(db, actor_id=1001, kind="message")
    _insert(db, actor_id=1002, kind="message")
    rows = recent_interactions(db, actor_id=1001, since_days=7)
    assert all(int(r["actors"][0]["id"]) == 1001 for r in rows)
    db.rollback()


def test_recent_interactions_kind_filter(db_session):
    from hermes.skills._utils.interactions_query import recent_interactions
    db = db_session
    _insert(db, actor_id=1003, kind="copilot_message")
    _insert(db, actor_id=1003, kind="ai_action")
    rows = recent_interactions(db, actor_id=1003, kind="ai_action", since_days=7)
    assert all(r["kind"] == "ai_action" for r in rows)
    assert any(int(r["actors"][0]["id"]) == 1003 for r in rows)
    db.rollback()


def test_recent_interactions_since_days(db_session):
    from hermes.skills._utils.interactions_query import recent_interactions
    db = db_session
    old = dt.datetime.utcnow() - dt.timedelta(days=30)
    new = dt.datetime.utcnow() - dt.timedelta(days=2)
    _insert(db, actor_id=1004, kind="message", at=old)
    _insert(db, actor_id=1004, kind="message", at=new)
    rows = recent_interactions(db, actor_id=1004, since_days=7)
    # Only the recent one (convert to naive for comparison with utcnow)
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=7)
    assert all(_to_naive(r["occurred_at"]) >= cutoff for r in rows)
    db.rollback()
