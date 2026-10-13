"""``DbWrapper.after_commit`` — defer side-effects until the txn lands.

Covers the contract documented on the method:

* fires once, after the underlying psycopg connection commits
* preserves registration order
* drops queued callbacks on rollback (explicit or via exception)
* fires immediately when the wrapper has no pending write (read-only call
  site — caller still gets the side-effect, no leak across lifecycles)
* swallows callback exceptions so a side-effect crash never breaks the
  write that already succeeded
"""
from __future__ import annotations


class _FakeTxnStatus:
    IDLE = "idle"
    INTRANS = "intrans"


class _FakeInfo:
    def __init__(self, status):
        self.transaction_status = status


class _FakeConn:
    """Minimal psycopg3-shaped fake — only what DbWrapper touches."""

    def __init__(self, *, status=_FakeTxnStatus.INTRANS):
        self.info = _FakeInfo(status)
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1
        self.info.transaction_status = _FakeTxnStatus.IDLE

    def rollback(self):
        self.rollbacks += 1
        self.info.transaction_status = _FakeTxnStatus.IDLE


def _patch_psycopg_status(monkeypatch):
    """Make ``DbWrapper._conn_in_pending_write`` see our fake enum.

    The shared ``conftest.py`` replaces ``psycopg`` with a ``MagicMock``
    module so import-time DB code doesn't blow up; that mock has no
    ``.pq`` attribute. Inject one whose ``TransactionStatus.IDLE`` matches
    the values our ``_FakeConn`` writes into ``info.transaction_status``.
    """
    import sys
    import types

    pq_mod = types.ModuleType("psycopg.pq")
    pq_mod.TransactionStatus = _FakeTxnStatus  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg.pq", pq_mod)


def test_after_commit_fires_once_post_commit(monkeypatch):
    _patch_psycopg_status(monkeypatch)
    from hermes.crm.db import DbWrapper

    conn = _FakeConn(status=_FakeTxnStatus.INTRANS)
    db = DbWrapper(conn)

    fired = []
    db.after_commit(lambda: fired.append("a"))
    db.after_commit(lambda: fired.append("b"))

    # Not yet — commit hasn't happened.
    assert fired == []
    db._maybe_commit(True)
    assert fired == ["a", "b"]
    assert conn.commits == 1

    # Drained — second commit must not re-fire.
    db._maybe_commit(True)
    assert fired == ["a", "b"]


def test_after_commit_drops_callbacks_on_rollback(monkeypatch):
    _patch_psycopg_status(monkeypatch)
    from hermes.crm.db import DbWrapper

    conn = _FakeConn(status=_FakeTxnStatus.INTRANS)
    db = DbWrapper(conn)

    fired = []
    db.after_commit(lambda: fired.append("x"))
    db.rollback()

    assert fired == []
    assert conn.rollbacks == 1


def test_after_commit_drops_callbacks_on_transaction_exception(monkeypatch):
    _patch_psycopg_status(monkeypatch)
    from hermes.crm.db import DbWrapper

    conn = _FakeConn(status=_FakeTxnStatus.INTRANS)
    db = DbWrapper(conn)

    fired = []
    try:
        with db.transaction():
            db.after_commit(lambda: fired.append("never"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert fired == []
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_after_commit_fires_on_outermost_transaction_exit(monkeypatch):
    _patch_psycopg_status(monkeypatch)
    from hermes.crm.db import DbWrapper

    conn = _FakeConn(status=_FakeTxnStatus.INTRANS)
    db = DbWrapper(conn)

    fired = []
    with db.transaction():
        # Nested transaction shouldn't fire on inner exit.
        with db.transaction():
            db.after_commit(lambda: fired.append("once"))
            assert fired == []
        assert fired == []  # inner block didn't commit
    assert fired == ["once"]
    assert conn.commits == 1


def test_after_commit_fires_eagerly_when_no_pending_write(monkeypatch):
    _patch_psycopg_status(monkeypatch)
    from hermes.crm.db import DbWrapper

    conn = _FakeConn(status=_FakeTxnStatus.IDLE)
    db = DbWrapper(conn)

    fired = []
    db.after_commit(lambda: fired.append("eager"))

    # Nothing pending → callback should have run immediately so it doesn't
    # leak into the next request that reuses this connection from the pool.
    assert fired == ["eager"]
    assert conn.commits == 0


def test_after_commit_swallows_callback_exceptions(monkeypatch, caplog):
    _patch_psycopg_status(monkeypatch)
    from hermes.crm.db import DbWrapper

    conn = _FakeConn(status=_FakeTxnStatus.INTRANS)
    db = DbWrapper(conn)

    fired = []

    def _bad():
        raise ValueError("intentional")

    db.after_commit(_bad)
    db.after_commit(lambda: fired.append("after_bad"))

    # Must not raise — the commit already happened, side-effect failures
    # are not write failures.
    db._maybe_commit(True)
    assert fired == ["after_bad"]


def test_emit_routes_via_after_commit_inside_caller_transaction(monkeypatch):
    """End-to-end-ish: when caller wraps emit() in their own ``transaction()``
    block, emit's INSERT does **not** auto-commit (depth>0). The route
    call must defer until the caller's outermost commit succeeds — not
    fire mid-transaction, where a celery worker could race the row.
    """
    _patch_psycopg_status(monkeypatch)
    from hermes.crm.db import DbWrapper
    from hermes_cloud.app.interactions import emitter

    conn = _FakeConn(status=_FakeTxnStatus.INTRANS)
    db = DbWrapper(conn)

    routed = []

    # ``emitter._route`` is a closure inside ``emit()``; patch the symbol
    # it imports so the inner call lands in our capture list.
    import sys
    import types
    fake_router = types.ModuleType("hermes_cloud.app.interactions.router")
    fake_router.route_new_interaction = lambda *, interaction_id, kind, profile_id: (
        routed.append(interaction_id)
    )
    monkeypatch.setitem(
        sys.modules, "hermes_cloud.app.interactions.router", fake_router,
    )

    # Stub fetchone so emit's INSERT…RETURNING returns a row.
    def _fake_fetchone(self, sql, args):
        return {"id": "test-uuid-1"}
    monkeypatch.setattr(DbWrapper, "fetchone", _fake_fetchone)

    with db.transaction():
        iid = emitter.emit(kind="chat", db=db)
        assert iid == "test-uuid-1"
        # depth>0 — emit's _maybe_commit was a no-op; route must NOT
        # have fired yet.
        assert routed == []

    # transaction() exit committed for real — route fires now.
    assert routed == ["test-uuid-1"]
    assert conn.commits == 1
