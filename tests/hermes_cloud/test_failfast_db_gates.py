"""Boot-time fail-fast DB gates — Charter §六定律 6 + §一定律 1.

The "可启动优先" InMemory fallback ladder lets create_app boot when
backing stores are unreachable. That's deliberate for unit tests + UI
shells, but in any environment that intends to be real (anything past
dev), a half-residue boot — process up, /api/me works, agents/audit/
CRM silently empty — is dangerous. Operators can't tell from the
outside that the deployment is broken.

These tests pin:

* Default behavior unchanged: ``HERMES_CLOUD_REQUIRE_DB`` unset and no
  ``session_store_url`` → boots with InMemory stores (test/dev path).
* Gate 1: ``HERMES_CLOUD_REQUIRE_DB=1`` + unset URL → fail.
* Gate 1: ``session_store_url=postgres://...`` + unreachable → fail.
* Gate 2: explicit ``HERMES_CRM_DATABASE_URL`` + CRM unreachable → fail.
* Gate 4: ``HERMES_CLOUD_REQUIRE_DB=1`` after Gates 1-2 pass but a store
  somehow resolved InMemory → fail (defensive — if Gates 1-2 are
  configured right this never trips, but a refactor that sneaks an
  InMemory back in is caught).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import (
    FailFastDbError, _apply_failfast_gates, _assert_no_inmemory_shadow,
    create_app,
)


def test_default_boot_succeeds_without_env_or_url(monkeypatch):
    """Test/dev path: no env var, no URL → InMemory stores are fine."""
    monkeypatch.delenv("HERMES_CLOUD_REQUIRE_DB", raising=False)
    monkeypatch.delenv("HERMES_CRM_DATABASE_URL", raising=False)
    monkeypatch.delenv("CRM_DATABASE_URL", raising=False)
    # Should not raise.
    app = create_app(AppConfig())
    assert app is not None


def test_gate1_require_db_without_url_fails(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_REQUIRE_DB", "1")
    monkeypatch.delenv("HERMES_CRM_DATABASE_URL", raising=False)
    with pytest.raises(FailFastDbError, match="Gate 1.*REQUIRE_DB"):
        _apply_failfast_gates(AppConfig())


def test_gate1_postgres_url_unreachable_fails(monkeypatch):
    """Set session_store_url to a postgres URL that won't connect → fail."""
    monkeypatch.delenv("HERMES_CLOUD_REQUIRE_DB", raising=False)
    cfg = AppConfig(
        session_store_url="postgresql://nobody@127.0.0.1:9/no_such_db",
    )
    with pytest.raises(FailFastDbError, match="Gate 1.*unreachable"):
        _apply_failfast_gates(cfg)


def test_gate1_sqlite_url_passes(monkeypatch, tmp_path):
    """Sqlite URL is not Charter-conformant for prod, but it's the
    test/dev default and Gate 1 must not block it (REQUIRE_DB unset)."""
    monkeypatch.delenv("HERMES_CLOUD_REQUIRE_DB", raising=False)
    cfg = AppConfig(
        session_store_url=f"sqlite:///{tmp_path}/test.db",
    )
    # Should not raise.
    _apply_failfast_gates(cfg)


def test_gate2_explicit_crm_url_unreachable_fails(monkeypatch):
    monkeypatch.setenv("HERMES_CRM_DATABASE_URL",
                       "postgresql://nobody@127.0.0.1:9/no_such_crm")
    monkeypatch.delenv("HERMES_CLOUD_REQUIRE_DB", raising=False)

    # Patch _crm_db_reachable to return False (it would anyway, but this
    # speeds the test by avoiding the real 30s pool retry).
    with patch("hermes_cloud.app.main._crm_db_reachable", return_value=False):
        with pytest.raises(FailFastDbError, match="Gate 2.*HERMES_CRM_DATABASE_URL"):
            _apply_failfast_gates(AppConfig())


def test_gate2_localhost_default_does_not_block(monkeypatch):
    """When neither HERMES_CRM_DATABASE_URL nor CRM_DATABASE_URL is set,
    crm.db._resolve_dsn synthesizes a localhost DSN. Gate 2 must NOT
    fail in that case — it lets the legacy fallback ladder warn instead.
    Otherwise every dev box without a local PG would refuse to boot."""
    monkeypatch.delenv("HERMES_CRM_DATABASE_URL", raising=False)
    monkeypatch.delenv("CRM_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_CLOUD_REQUIRE_DB", raising=False)

    with patch("hermes_cloud.app.main._crm_db_reachable", return_value=False):
        # Should not raise — only warn.
        _apply_failfast_gates(AppConfig())


def test_gate4_inmemory_shadow_under_require_db_fails(monkeypatch):
    """If Gates 1-2 pass somehow but a store still resolved InMemory,
    Gate 4 catches it. This is the safety net for future refactor drift."""
    monkeypatch.setenv("HERMES_CLOUD_REQUIRE_DB", "1")

    class _FakeState:
        pass

    from hermes_cloud.app.auth_store import InMemoryAuthStore
    state = _FakeState()
    state.auth_store = InMemoryAuthStore()
    # Other stores set to something non-InMemory to isolate the failure.
    state.config_store = object()
    state.channel_store = object()
    state.session_store = object()
    state.profile_store = object()
    state.adapter_store = object()

    with pytest.raises(FailFastDbError, match="Gate 4"):
        _assert_no_inmemory_shadow(state)


def test_gate4_silent_when_require_db_unset(monkeypatch):
    """Default test path uses InMemory stores — Gate 4 must stay quiet."""
    monkeypatch.delenv("HERMES_CLOUD_REQUIRE_DB", raising=False)

    class _FakeState:
        pass

    from hermes_cloud.app.auth_store import InMemoryAuthStore
    state = _FakeState()
    state.auth_store = InMemoryAuthStore()
    # Should not raise.
    _assert_no_inmemory_shadow(state)
