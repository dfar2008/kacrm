"""conftest for hermes_cloud tests.

Stubs psycopg and related DB packages so tests that monkeypatch db_session
can import hermes.crm.db without a live PostgreSQL install.
Tests that actually need DB access use MagicMock-based DbWrapper fakes.

Integration mode:
  Set ``HERMES_TESTS_REAL_PG=1`` to skip stubbing and run against a real
  PostgreSQL (requires DB up and reachable via the same DSN the app uses).
  Tests that need real PG should use the ``requires_real_pg`` marker so
  they auto-skip when the env var is unset.

      pytestmark = pytest.mark.requires_real_pg

  Run integration tests:
      HERMES_TESTS_REAL_PG=1 uv run pytest -m requires_real_pg tests/...
"""
from __future__ import annotations

import os
import secrets
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Worktree support: ensure hermes/crm (CRM-extended hermes) is importable.
# In git worktrees the hermes submodule may be a vanilla upstream clone
# without the crm/ subtree. Walk up the directory tree to find the parent
# hermes-cloud checkout that has the full tree and add it to sys.path so
# `hermes.crm` resolves.
# ---------------------------------------------------------------------------
_LOCAL_HERMES = Path(__file__).resolve().parents[2] / "hermes"
if not (_LOCAL_HERMES / "crm").is_dir():
    _p = _LOCAL_HERMES
    for _depth in range(8):
        _p = _p.parent
        if (_p / "hermes" / "crm").is_dir():
            # Append (not prepend) so the worktree's own packages (hermes_cloud,
            # etc.) are resolved first; this path only fills in missing modules
            # like hermes.crm that don't exist in the submodule checkout.
            if str(_p) not in sys.path:
                sys.path.append(str(_p))
            break


# ---------------------------------------------------------------------------
# Integration-mode toggle (decisions plan task 1.5)
# ---------------------------------------------------------------------------

_REAL_PG = os.environ.get("HERMES_TESTS_REAL_PG") == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_real_pg: test requires a live PostgreSQL "
        "(skipped unless HERMES_TESTS_REAL_PG=1 is set).",
    )


def pytest_collection_modifyitems(config, items):
    if _REAL_PG:
        return
    skip = pytest.mark.skip(reason="requires real PG (set HERMES_TESTS_REAL_PG=1)")
    for item in items:
        if "requires_real_pg" in item.keywords:
            item.add_marker(skip)


# Modules that must stay real for the whole session. Collection imports every
# test module's top-level code; a stray ``sys.modules.setdefault(name,
# MagicMock())`` there would silently replace one of these with a mock and
# surface as dozens of unrelated assertion failures in later files.
_MUST_STAY_REAL = (
    "hermes.crm.db",
    "hermes.crm.functions",
    "hermes.crm.save",
    "hermes.crm.list_query",
    "hermes.memory.ingest",
    "hermes_cloud.app.db",
)


_TEST_EMAIL_PATTERNS = (
    "pqa-%@example.com",
    "sales_e2e%",
    "nonadmin%",
)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Belt-and-suspenders cleanup: hard-delete any test-fixture users
    left behind by tests that crashed before their teardown ran.

    Per-fixture teardowns already try to clean up, but a SIGINT or
    pytest-internal failure during the test body skips them — that
    leak is how production user-management pages ended up showing 60+
    ``pqa-XXXX@example.com`` rows. Sweep them at session end so the
    next run starts clean. Real-PG only — the stubbed path has no DB.
    """
    if not _REAL_PG:
        return
    try:
        from hermes.crm.db import db_session
    except Exception:
        return
    try:
        with db_session() as db:
            for pat in _TEST_EMAIL_PATTERNS:
                # Schema fact (charter v2 §简化原则): profiles has no
                # user_id column — the linkage is profiles.id ==
                # users.username. Delete profiles first via that join
                # so the boot-loop in main.py doesn't resurrect a
                # gateway for the dead user on the next cloud restart;
                # then hard-delete the user row.
                db.execute(
                    "DELETE FROM profiles WHERE id IN "
                    "(SELECT username FROM users WHERE email LIKE %s)",
                    (pat,),
                )
                db.execute("DELETE FROM users WHERE email LIKE %s", (pat,))
    except Exception:
        # Never let a sweep error mask the real test result.
        pass


def pytest_collection_finish(session: pytest.Session) -> None:
    """Fail fast if collection replaced a real module with a MagicMock.

    psycopg and friends are stubbed on purpose (see above); these modules
    are not. If any was mocked, a test module is stubbing ``sys.modules`` at
    import time — fix that there, do not stub real modules globally.
    """
    poisoned = sorted(
        name
        for name in _MUST_STAY_REAL
        if isinstance(sys.modules.get(name), MagicMock)
    )
    if poisoned:
        raise pytest.UsageError(
            "Real modules replaced by MagicMock during test collection: "
            + ", ".join(poisoned)
            + " — a test module is stubbing sys.modules at import time. "
            "Stub heavy dependencies via a fixture or this conftest instead."
        )


# Make tinker_atropos importable for config_init integration tests.
_TINKER_ATROPOS_DIR = Path(__file__).resolve().parents[2] / "hermes" / "tinker-atropos"
if _TINKER_ATROPOS_DIR.exists() and str(_TINKER_ATROPOS_DIR) not in sys.path:
    sys.path.insert(0, str(_TINKER_ATROPOS_DIR))


def _stub_if_missing(name: str, parent: str | None = None) -> MagicMock:
    if name in sys.modules:
        return sys.modules[name]
    mod = MagicMock(spec=types.ModuleType(name))
    sys.modules[name] = mod
    if parent and parent in sys.modules:
        setattr(sys.modules[parent], name.split(".")[-1], mod)
    return mod


if not _REAL_PG:
    for _name, _parent in [
        ("psycopg", None),
        ("psycopg.rows", "psycopg"),
        ("psycopg.connection", "psycopg"),
        ("pgvector", None),
        ("pgvector.psycopg", "pgvector"),
        ("psycopg_pool", None),
    ]:
        _stub_if_missing(_name, _parent)

    # psycopg.rows.dict_row must be callable (used as row_factory)
    sys.modules["psycopg.rows"].dict_row = MagicMock(return_value={})
    # pgvector.psycopg.register_vector must be callable (called at module init)
    sys.modules["pgvector.psycopg"].register_vector = MagicMock()

    # ConnectionPool raises on construction so _crm_reachable() returns False
    # and live-DB tests are correctly skipped in environments without a real DB.
    class _NoPoolError(RuntimeError):
        pass

    class _FakeConnectionPool:
        def __init__(self, *args, **kwargs):
            raise _NoPoolError("no real PostgreSQL in test environment — stub only")

    sys.modules["psycopg_pool"].ConnectionPool = _FakeConnectionPool


@pytest.fixture(autouse=True)
def _isolate_rl_dlq_dir(tmp_path, monkeypatch):
    """Keep RL DLQ writes from tests out of the operator's real ~/.hermes dir."""
    monkeypatch.setenv("HERMES_RL_DLQ_DIR", str(tmp_path / "rl_dlq"))


@pytest.fixture
def pg_users():
    """Factory creating throwaway CRM users for the real-PG profile tests.

    Yields ``make(n=1) -> list[SimpleNamespace]`` where each namespace has:

      * ``uid``      — decimal ``users.id`` (the protocol-level user_id str)
      * ``username`` — ``users.username`` (== the user's sole profile.id
        under Charter v2 §简化原则, "1 user = 1 profile")
      * ``token``    — a Bearer api_key to seed into ``AppConfig.api_keys``

    Teardown deletes each user's profile row (linked by
    ``profiles.id == users.username`` — there is no ``user_id`` column) then
    soft-deletes the user. The ``pqa-%@example.com`` email also makes the
    conftest session-finish sweep hard-delete any row a crashed test leaks.

    Real-PG only; tests must carry ``pytestmark = requires_real_pg``.
    """
    from types import SimpleNamespace

    from hermes.crm.db import db_session
    from hermes_cloud.app.crm_auth_store import CrmUsersAuthStore

    auth = CrmUsersAuthStore()
    created: list[SimpleNamespace] = []

    def make(n: int = 1) -> list[SimpleNamespace]:
        out: list[SimpleNamespace] = []
        for _ in range(n):
            email = f"pqa-{secrets.token_hex(4)}@example.com"
            acct = auth.register_user(email, "pw")
            with db_session() as db:
                username = db.fetchcolumn(
                    "SELECT username FROM users WHERE id = %s", (int(acct.id),)
                )
            ns = SimpleNamespace(
                uid=acct.id, username=str(username), token=f"sk-{acct.id}"
            )
            created.append(ns)
            out.append(ns)
        return out

    yield make

    with db_session() as db:
        for ns in created:
            db.execute(
                "DELETE FROM profiles WHERE id IN "
                "(SELECT username FROM users WHERE id = %s)",
                (int(ns.uid),),
            )
            db.execute(
                "UPDATE users SET deleted = TRUE WHERE id = %s", (int(ns.uid),)
            )
