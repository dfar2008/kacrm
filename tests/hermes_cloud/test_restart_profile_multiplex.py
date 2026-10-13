"""restart_profile behavior across the multiplex flag.

  - flag OFF: restart_profile still does stop_profile + ensure_running (the
    per-profile subprocess path — byte-for-byte unchanged);
  - flag ON: restart_profile must NOT stop_profile (that would SIGTERM the
    shared mux gateway = all tenants offline). Instead it ensures the shared
    mux is up and POSTs the internal reload-adapters endpoint, returning the
    mux port.

Nothing real is spawned or connected — subprocess + httpx are mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hermes_cloud.app.profile_manager import ProfileManager


def _manager(tmp_path):
    return ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
        port_range_start=9100,
        port_range_end=9110,
    )


@pytest.mark.asyncio
async def test_restart_profile_flag_off_stops_and_ensures(tmp_path, monkeypatch):
    """Flag off (explicit opt-out) → stop_profile + ensure_running, as today."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")
    manager = _manager(tmp_path)

    with patch.object(manager, "stop_profile", new=AsyncMock()) as stop_spy, \
        patch.object(manager, "ensure_running", new=AsyncMock(return_value=9107)) as ensure_spy, \
        patch.object(manager, "_ensure_mux_running", new=AsyncMock()) as mux_spy, \
        patch("hermes_cloud.app.profile_manager.httpx.AsyncClient") as httpx_cls:
        port = await manager.restart_profile("alice", user_id="alice")

    assert port == 9107
    stop_spy.assert_awaited_once_with("alice")
    ensure_spy.assert_awaited_once_with("alice", user_id="alice")
    mux_spy.assert_not_called()
    httpx_cls.assert_not_called()


@pytest.mark.asyncio
async def test_restart_profile_flag_on_hot_reloads(tmp_path, monkeypatch):
    """Flag on → never stop_profile; ensure mux up + POST reload endpoint."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)

    # Capture the POST issued through the async context-managed httpx client.
    post_mock = AsyncMock(return_value=type("R", (), {"status_code": 200})())

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        post = post_mock

    with patch.object(manager, "stop_profile", new=AsyncMock()) as stop_spy, \
        patch.object(manager, "ensure_running", new=AsyncMock()) as ensure_spy, \
        patch.object(manager, "_ensure_mux_running", new=AsyncMock(return_value=9100)) as mux_spy, \
        patch("hermes_cloud.app.profile_manager.httpx.AsyncClient", FakeClient):
        port = await manager.restart_profile("alice", user_id="alice")

    assert port == 9100
    # Shared gateway must NOT be stopped, and the per-profile spawn path is skipped.
    stop_spy.assert_not_called()
    ensure_spy.assert_not_called()
    mux_spy.assert_awaited_once_with("alice")

    # Exactly one reload POST to the profile-scoped endpoint with the bearer key.
    post_mock.assert_awaited_once()
    args, kwargs = post_mock.call_args
    assert args[0] == "http://127.0.0.1:9100/internal/profiles/alice/reload-adapters"
    assert kwargs["headers"]["Authorization"] == "Bearer dev-internal-key"


@pytest.mark.asyncio
async def test_restart_profile_flag_on_reload_failure_nonfatal(tmp_path, monkeypatch):
    """A failed reload POST must not raise; still returns the mux port."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    with patch.object(manager, "stop_profile", new=AsyncMock()) as stop_spy, \
        patch.object(manager, "_ensure_mux_running", new=AsyncMock(return_value=9100)), \
        patch("hermes_cloud.app.profile_manager.httpx.AsyncClient", FakeClient):
        port = await manager.restart_profile("alice", user_id="alice")

    assert port == 9100
    stop_spy.assert_not_called()
