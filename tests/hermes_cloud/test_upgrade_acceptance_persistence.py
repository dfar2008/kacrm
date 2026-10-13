"""Upgrade-acceptance — session persistence contract (module B).

Guards the exact regression classes the v0.18 upgrade actually produced:
  * sqlite ``append_message`` blew up on a cloud-only kwarg (``duration_ms``)
    because upstream's signature didn't absorb extra fields.
  * ``set_turn_meta`` timing / signature drift.
  * the nudge counters that the closed-learning-loop rides on (self-evolution)
    not being hydrated from history on stateless surfaces.

If an upgrade changes any of these signatures, chat turns silently stop
persisting (messages vanish on refresh) — the worst kind of break because
nothing errors loudly. These are pure signature/contract checks.
"""

from __future__ import annotations

import inspect

import pytest


def _has_var_keyword(func) -> bool:
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        for p in inspect.signature(func).parameters.values()
    )


# ── sqlite (_SqliteSessionDB) — upstream impl, must absorb cloud kwargs ───────

def test_sqlite_append_message_absorbs_kwargs():
    from hermes_state import _SqliteSessionDB
    assert _has_var_keyword(_SqliteSessionDB.append_message), (
        "sqlite append_message must accept **kwargs so cloud-only storage "
        "fields (duration_ms / turn_meta) don't TypeError (the v0.18 regression)"
    )


def test_sqlite_sessiondb_core_methods():
    # set_turn_meta is a cloud-only (PgSessionDB) method — the upstream sqlite
    # impl carries only the core transcript methods.
    from hermes_state import _SqliteSessionDB
    for m in ("create_session", "append_message", "get_messages"):
        assert callable(getattr(_SqliteSessionDB, m, None)), f"sqlite SessionDB lost {m}"


# ── PgSessionDB (cloud impl) — parity with the upstream contract ─────────────

def test_pg_append_message_absorbs_kwargs():
    from hermes_cloud.app.agent_session_pg import PgSessionDB
    assert _has_var_keyword(PgSessionDB.append_message), (
        "PgSessionDB append_message must accept **kwargs (interface parity)"
    )


def test_pg_sessiondb_core_methods():
    from hermes_cloud.app.agent_session_pg import PgSessionDB
    for m in ("create_session", "append_message", "set_turn_meta"):
        assert callable(getattr(PgSessionDB, m, None)), f"PgSessionDB lost {m}"


def test_pg_create_session_binds_user_id():
    """create_session must read the X-Hermes-Cloud-User-Id contextvar so
    agent_sessions get the right owner (else chatbox turns land under the
    wrong / null user and 'vanish' from the SPA)."""
    import hermes_cloud.app.agent_session_pg as pg
    src = open(pg.__file__, encoding="utf-8").read()
    assert "user_id" in inspect.getsource(pg.PgSessionDB.create_session)


# ── Nudge-counter hydration (self-evolution closed loop) ─────────────────────

def test_turn_context_hydrates_both_nudge_counters():
    """turn_context must recover BOTH the memory counter (upstream #22357) and
    the skill counter (our completion) from persisted history — otherwise the
    autonomous skill/memory review never fires on stateless api_server turns."""
    import agent.turn_context as tc
    src = open(tc.__file__, encoding="utf-8").read()
    assert "_turns_since_memory" in src and "prior_user_turns" in src, "memory hydration gone"
    assert "_iters_since_skill" in src and "prior_iters" in src, "skill hydration gone"


# ── Persist-failure must be loud, not silent ─────────────────────────────────

def test_flush_failure_is_logged_as_error(caplog, monkeypatch):
    from types import SimpleNamespace
    import logging
    import agent.session_persistence as persistence
    from run_agent import AIAgent

    assert issubclass(AIAgent, persistence.SessionPersistenceMixin)
    monkeypatch.setattr(persistence, "_db_flush_collect", lambda *args: ([], []))
    def failed_write(*args):
        raise RuntimeError("test store unavailable")
    monkeypatch.setattr(persistence, "_db_flush_write", failed_write)
    agent = SimpleNamespace(_session_db=object(), _session_db_created=True, session_id="test")
    with caplog.at_level(logging.ERROR):
        result = persistence.SessionPersistenceMixin._flush_messages_to_session_db_unlocked(agent, [])
    assert result is False
    assert "SessionDB flush FAILED" in caplog.text
    assert "test store unavailable" in caplog.text
