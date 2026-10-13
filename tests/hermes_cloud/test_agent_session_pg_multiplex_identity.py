"""Multi-tenant attribution for IM-originated agent sessions (R1-3, HIGH).

``PgSessionDB.create_session`` used to overwrite an IM sender's openid with
the process-level ``CRM_USER_ID`` env var for every IM source. Under the
multiplex single-process gateway that env is deliberately unset (identity is
resolved per turn), so the old path either dropped the session onto the raw
external openid (invisible in the inbox) or — if the env leaked in — collapsed
every tenant's IM sessions onto one user_id (cross-tenant leak).

The fix resolves identity per turn from the active profile scope (reusing
``identity.current_user_id_int``) when multiplexing is active, and leaves the
non-multiplex path byte-for-byte identical (still reads ``CRM_USER_ID``).
"""
from __future__ import annotations

import sys

import pytest

from hermes_cloud.app import agent_session_pg
from hermes_cloud.app import identity
# NOTE: import the override setters from the SAME module object
# ``identity`` resolved, not by a hard-coded path. ``hermes_constants`` is
# importable under TWO names (top-level inside the gateway subprocess where
# PYTHONPATH=hermes, and ``hermes.hermes_constants`` in the cloud process).
# Each spelling is a DISTINCT module object with its own ContextVar, so
# writing the override through one and reading it through the other silently
# resolves to "no scope" — which surfaced here as an UnscopedIdentityError
# from a test that had, in fact, set the scope.
try:  # pragma: no cover - mirrors identity.py's own import shim
    from hermes_constants import (
        set_hermes_home_override,
        reset_hermes_home_override,
    )
except ImportError:  # pragma: no cover
    from hermes.hermes_constants import (
        set_hermes_home_override,
        reset_hermes_home_override,
    )


# ``secret_scope`` is importable under TWO module names — top-level
# ``agent.secret_scope`` (how the gateway subprocess sees it, PYTHONPATH=hermes)
# and ``hermes.agent.secret_scope`` (how the cloud process sees it). Each alias
# is a distinct module object with its OWN ``_MULTIPLEX_ACTIVE`` global. In
# production only ONE alias is ever live per process, so setter and reader agree
# (see the shim comments in identity.py / agent_session_pg.py). But the test
# harness (venv-mux editable install) exposes BOTH aliases at once, and the
# code-under-test may resolve is_multiplex_active from a different alias than a
# module-level ``from agent.secret_scope import set_multiplex_active`` binds —
# so flipping one alias' flag would be invisible to the reader (green in
# isolation, red in the full suite, depending on import order).
#
# Set the flag on EVERY loaded alias so the intended value is observed no matter
# which one any code path resolves. This is a harness-only compensation for the
# dual-alias artifact; it changes no production behavior.
def set_multiplex_active(active: bool) -> None:
    seen: set[int] = set()
    for name in ("agent.secret_scope", "hermes.agent.secret_scope"):
        mod = sys.modules.get(name)
        if mod is None:
            try:  # pragma: no cover - import-shim
                mod = __import__(name, fromlist=["set_multiplex_active"])
            except ImportError:
                continue
        if id(mod) in seen:
            continue
        seen.add(id(mod))
        mod.set_multiplex_active(active)


class _FakeCursor:
    """db_session stand-in serving both identity's SELECT and the INSERT."""

    def __init__(self, slug_to_id: dict[str, int], captured: dict) -> None:
        self._slug_to_id = slug_to_id
        self._captured = captured

    def fetchone(self, sql, params=None):
        # identity.current_user_id_int -> SELECT id FROM users WHERE username=%s
        slug = params[0]
        uid = self._slug_to_id.get(slug)
        return {"id": uid} if uid is not None else None

    def execute(self, sql, params=None):
        # create_session -> INSERT INTO agent_sessions (... user_id is params[2])
        if "INSERT INTO agent_sessions" in sql:
            self._captured["user_id"] = params[2]


class _FakeDbCtx:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _FakeCursor:
        return self._cursor

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def captured(monkeypatch):
    """Patch ``hermes.crm.db.db_session`` (the symbol both call sites import)
    with an in-memory fake and return the dict that records the written
    ``user_id``."""
    cap: dict = {}
    cursor = _FakeCursor({"alice": 101, "bob": 202}, cap)
    monkeypatch.setattr("hermes.crm.db.db_session", lambda: _FakeDbCtx(cursor))
    return cap


@pytest.fixture(autouse=True)
def _reset_multiplex_and_cache():
    """Keep the process-global multiplex flag, the per-slug id cache, AND the
    context-local hermes_home override from bleeding between tests.

    The override is a ``ContextVar`` that persists across sync pytest tests, so
    an unrelated test elsewhere in the suite that sets a scope without resetting
    it would leak in here — silently satisfying ``fails_closed``'s "no scope"
    precondition and making the raise vanish (green in isolation, red in the
    full suite). ``set_hermes_home_override(None)`` forces the clean, unscoped
    state regardless of what leaked in; we reset it afterwards."""
    set_multiplex_active(False)
    identity._uid_cache.clear()
    home_tok = set_hermes_home_override(None)
    try:
        yield
    finally:
        reset_hermes_home_override(home_tok)
        set_multiplex_active(False)
        identity._uid_cache.clear()


def _create_im_session(db, captured, source="feishu", openid="ext_openid_xyz"):
    captured.clear()
    db.create_session(session_id="sess-1", source=source, user_id=openid)
    return captured.get("user_id")


def test_non_multiplex_reads_crm_user_id_env(captured, monkeypatch):
    """Legacy behavior unchanged: outside multiplex, IM sessions are attributed
    to the process-level CRM_USER_ID."""
    set_multiplex_active(False)
    monkeypatch.setenv("CRM_USER_ID", "42")

    db = agent_session_pg.PgSessionDB()
    assert _create_im_session(db, captured) == "42"


def test_multiplex_resolves_per_profile_scope_no_crosstalk(captured, monkeypatch):
    """Under multiplex the per-turn profile scope decides attribution; the
    process CRM_USER_ID env is NOT read (that is the crosstalk source)."""
    set_multiplex_active(True)
    # Prove the env is ignored under multiplex: a bogus value must not win.
    monkeypatch.setenv("CRM_USER_ID", "999")

    db = agent_session_pg.PgSessionDB()

    tok = set_hermes_home_override("/srv/profiles/alice")
    try:
        assert _create_im_session(db, captured) == "101"
    finally:
        reset_hermes_home_override(tok)

    tok = set_hermes_home_override("/srv/profiles/bob")
    try:
        assert _create_im_session(db, captured) == "202"
    finally:
        reset_hermes_home_override(tok)


def test_multiplex_without_scope_fails_closed(captured, monkeypatch):
    """Multiplex on + no per-turn scope must raise (fail-closed), never
    silently attribute to a default tenant."""
    set_multiplex_active(True)
    monkeypatch.delenv("CRM_USER_ID", raising=False)

    db = agent_session_pg.PgSessionDB()
    with pytest.raises(identity.UnscopedIdentityError):
        db.create_session(session_id="sess-x", source="feishu", user_id="ext_openid")


def test_non_im_source_untouched_under_multiplex(captured, monkeypatch):
    """Non-IM sources never hit the identity-override branch: a chatbox/copilot
    session keeps whatever user_id it was created with, even under multiplex."""
    set_multiplex_active(True)
    db = agent_session_pg.PgSessionDB()

    captured.clear()
    db.create_session(session_id="sess-c", source="chatbox", user_id="7")
    assert captured.get("user_id") == "7"
