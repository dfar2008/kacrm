"""``agent_messages_retention`` celery beat task — Charter §一原语 ①.

Pins the retention contract: prune ended-and-aged sessions, keep
active sessions + recently-ended sessions intact. Active sessions
must NEVER lose messages because the agent loop's compaction reads
them via PgSessionDB.get_messages.

These tests use a mocked db_session so they run in any environment
(no live PG required); the SQL semantics were also verified manually
against hermes_crm_dev.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_db_session(*, count=0, deleted_ids=None):
    """Patch crm.db.db_session so the retention task sees scripted rows."""
    fake_db = MagicMock()
    fake_db.fetchcolumn.return_value = count
    fake_db.fetchall.return_value = [
        {"id": sid} for sid in (deleted_ids or [])
    ]
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes.crm.db.db_session", return_value=cm), fake_db


def test_retention_zero_days_skips():
    """``retention_days <= 0`` opts the cluster out (compliance archive
    mode). Task returns immediately without DB hits."""
    from hermes_cloud.app.celery_app import agent_messages_retention
    out = agent_messages_retention(retention_days=0)
    assert out == {"deleted_sessions": 0, "deleted_messages": 0,
                   "skipped": True}
    out = agent_messages_retention(retention_days=-1)
    assert out["skipped"] is True


def test_retention_default_pulls_from_env(monkeypatch):
    """Absent param → reads ``HERMES_AGENT_MESSAGES_RETENTION_DAYS``;
    falls back to 90 if env unset / malformed."""
    from hermes_cloud.app.celery_app import agent_messages_retention

    p, _ = _mock_db_session(count=0, deleted_ids=[])
    with p, monkeypatch.context() as m:
        m.delenv("HERMES_AGENT_MESSAGES_RETENTION_DAYS", raising=False)
        # Should not raise; default 90.
        out = agent_messages_retention()
        assert out["skipped"] is False

    with p, monkeypatch.context() as m:
        m.setenv("HERMES_AGENT_MESSAGES_RETENTION_DAYS", "not-a-number")
        out = agent_messages_retention()
        # Malformed → default 90, still runs (skipped=False).
        assert out["skipped"] is False


def test_retention_returns_deleted_counts():
    from hermes_cloud.app.celery_app import agent_messages_retention
    p, fake_db = _mock_db_session(count=42, deleted_ids=["s1", "s2", "s3"])
    with p:
        out = agent_messages_retention(retention_days=90)
    assert out == {"deleted_sessions": 3, "deleted_messages": 42,
                   "skipped": False}


def test_retention_sql_only_targets_ended_sessions():
    """The DELETE WHERE clause must require ``ended_at IS NOT NULL``
    so active sessions (long-running agent loops) don't lose context.

    This is the most important contract — a regression here silently
    deletes an active session's history mid-run.
    """
    import inspect
    from hermes_cloud.app import celery_app
    src = inspect.getsource(celery_app.agent_messages_retention)
    # Both the DELETE and the count COALESCE/JOIN must filter on
    # ended_at IS NOT NULL.
    assert "ended_at IS NOT NULL" in src
    # The DELETE specifically must filter (not just the count subquery).
    delete_block = src[src.index("DELETE FROM agent_sessions"):]
    assert "ended_at IS NOT NULL" in delete_block[:300]


def test_retention_logs_summary_when_pruning(caplog):
    """When pruning happens, log a summary with sessions/messages/days
    so operators can audit retention activity."""
    import logging
    from hermes_cloud.app.celery_app import agent_messages_retention
    p, _ = _mock_db_session(count=10, deleted_ids=["a", "b"])
    caplog.set_level(logging.INFO, logger="hermes_cloud.app.celery_app")
    with p:
        agent_messages_retention(retention_days=90)
    log_msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "agent_messages_retention pruned" in m
        and "sessions=2" in m and "messages=10" in m and "days=90" in m
        for m in log_msgs
    )


def test_retention_silent_when_no_rows(caplog):
    """Empty pruning runs should NOT spam the log every day."""
    import logging
    from hermes_cloud.app.celery_app import agent_messages_retention
    p, _ = _mock_db_session(count=0, deleted_ids=[])
    caplog.set_level(logging.INFO, logger="hermes_cloud.app.celery_app")
    with p:
        agent_messages_retention(retention_days=90)
    log_msgs = [r.getMessage() for r in caplog.records]
    assert not any("agent_messages_retention pruned" in m for m in log_msgs)


def test_retention_db_error_propagates():
    """A DB exception should surface (the celery task wrapper's retry
    policy decides what to do); we shouldn't swallow + lie about deleted
    counts."""
    from hermes_cloud.app.celery_app import agent_messages_retention
    cm = MagicMock()
    cm.__enter__ = MagicMock(side_effect=RuntimeError("PG down"))
    cm.__exit__ = MagicMock(return_value=False)
    with patch("hermes.crm.db.db_session", return_value=cm):
        with pytest.raises(RuntimeError, match="PG down"):
            agent_messages_retention(retention_days=90)
