"""Multiplex (B2) gated behavior for ProfileManager.

All new logic is gated on HERMES_CLOUD_MULTIPLEX. These tests verify:
  - flag OFF: the mux flag reads False, mux_path_prefix is "", and
    ensure_running takes the per-profile path (never the mux branch);
  - flag ON: the flag reads True, mux_path_prefix is "/p/{id}", and any
    profile_id resolves to the SAME shared mux port with the mux gateway
    launched exactly once;
  - _configure_mux_root writes multiplex_profiles + the shared api_server port.

No real subprocess / gateway is started — spawn + health are mocked.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from hermes_cloud.app.profile_manager import ProfileManager


def _manager(tmp_path):
    return ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
        port_range_start=9100,
        port_range_end=9110,
    )


# ── flag OFF ────────────────────────────────────────────────────────────────

def test_multiplex_enabled_by_default(tmp_path, monkeypatch):
    """2026-07-24: multiplex 是默认拓扑 —— env 未设即启用。"""
    monkeypatch.delenv("HERMES_CLOUD_MULTIPLEX", raising=False)
    manager = _manager(tmp_path)
    assert manager._multiplex_enabled() is True


def test_multiplex_on_for_empty_and_truthy(tmp_path, monkeypatch):
    """未设/空/空白 与 truthy 取值都解析为启用(默认拓扑)。"""
    manager = _manager(tmp_path)
    for val in ("", "  ", "1", "true", "TRUE", "yes", "on", " on "):
        monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", val)
        assert manager._multiplex_enabled() is True, val


def test_multiplex_disabled_only_for_explicit_falsey(tmp_path, monkeypatch):
    """紧急 opt-out:只有显式 falsey 取值才回退到非 multiplex。"""
    manager = _manager(tmp_path)
    for val in ("0", "false", "FALSE", "no", "off", " off "):
        monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", val)
        assert manager._multiplex_enabled() is False, val


def test_mux_path_prefix_empty_when_explicitly_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")  # 显式 opt-out
    manager = _manager(tmp_path)
    assert manager.mux_path_prefix("alice") == ""
    assert manager.mux_path_prefix("bob") == ""


@pytest.mark.asyncio
async def test_ensure_running_takes_per_profile_path_when_disabled(
    tmp_path, monkeypatch
):
    """Flag off (explicit opt-out) → ensure_running goes through
    _start_for_profile, never the mux branch. The non-multiplex path must
    stay reachable and byte-for-byte unchanged."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")  # 显式 opt-out
    manager = _manager(tmp_path)

    class DummyProcess:
        pid = 4242
        returncode = None

    async def fake_spawn(*args, env=None, **kwargs):
        return DummyProcess()

    started_profiles: list[str] = []
    orig_start = manager._start_for_profile

    async def spy_start(profile_id, user_id):
        started_profiles.append(profile_id)
        return await orig_start(profile_id, user_id)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_spawn), \
        patch.object(manager, "_wait_healthy", return_value=True), \
        patch.object(manager, "_ensure_mux_running") as mux_spy, \
        patch.object(manager, "_start_for_profile", side_effect=spy_start):
        port = await manager.ensure_running("alice", user_id="alice")

    assert isinstance(port, int)
    mux_spy.assert_not_called()
    assert started_profiles == ["alice"]


# ── flag ON ─────────────────────────────────────────────────────────────────

def test_multiplex_enabled_for_truthy_values(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    for val in ("1", "true", "TRUE", "yes", "on", " on "):
        monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", val)
        assert manager._multiplex_enabled() is True, val


def test_mux_path_prefix_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)
    assert manager.mux_path_prefix("alice") == "/p/alice"
    assert manager.mux_path_prefix("bob") == "/p/bob"


@pytest.mark.asyncio
async def test_all_profiles_share_one_mux_port_launched_once(
    tmp_path, monkeypatch
):
    """Flag on → ensure_running for ANY profile returns the same mux port,
    and the mux gateway is launched exactly once."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)

    class DummyProcess:
        pid = 5555
        returncode = None

    launch_calls: list[int] = []

    async def fake_launch_mux(port, user_id=None):
        launch_calls.append(port)
        return DummyProcess(), None

    with patch.object(manager, "_launch_mux_process", side_effect=fake_launch_mux), \
        patch.object(manager, "_wait_healthy", return_value=True):
        port_a = await manager.ensure_running("alice", user_id="alice")
        port_b = await manager.ensure_running("bob", user_id="bob")
        port_a2 = await manager.ensure_running("alice", user_id="alice")

    assert port_a == port_b == port_a2
    # Deterministic fixed port = range's first free port.
    assert port_a == 9100
    # Launched exactly once despite three ensure_running calls / two profiles.
    assert launch_calls == [9100]
    # get_running_processes is multiplex-aware (2026-07-24): the shared mux is
    # fanned out to one row per served profile (all on the same port), and the
    # ``__mux__`` sentinel is NEVER surfaced as a profile the operator manages.
    rows = manager.get_running_processes()
    assert all(p["profile_id"] != "__mux__" for p in rows)
    assert all(p["port"] == 9100 and p.get("multiplexed") for p in rows)


@pytest.mark.asyncio
async def test_configure_mux_root_writes_multiplex_and_shared_port(tmp_path):
    manager = _manager(tmp_path)
    manager._configure_mux_root(9100)

    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert cfg["multiplex_profiles"] is True
    assert cfg["API_SERVER_ENABLED"] is True
    assert cfg["API_SERVER_HOST"] == "127.0.0.1"
    assert cfg["API_SERVER_PORT"] == 9100
    assert cfg["API_SERVER_KEY"] == "dev-internal-key"
    # Explicit platforms.api_server listener (pure-default has no messaging
    # platform, so the top-level flag alone did not reliably bind a socket).
    assert cfg["platforms"]["api_server"]["enabled"] is True
    assert cfg["platforms"]["api_server"]["extra"]["port"] == 9100


@pytest.mark.asyncio
async def test_launch_mux_process_uses_root_home_and_default_pin(
    tmp_path, monkeypatch
):
    """The mux launch path: HERMES_HOME=root, command pins -p default, and
    the per-profile-only CRM_USER_ID is NOT set process-wide."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)

    captured = {}

    class DummyProcess:
        pid = 6161
        returncode = None

    async def fake_spawn(*args, env=None, **kwargs):
        captured["argv"] = list(args)
        captured["env"] = env
        return DummyProcess()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_spawn):
        process, log_file = await manager._launch_mux_process(9100, user_id="alice")

    try:
        # HERMES_HOME points at the ROOT, not a per-profile dir.
        assert captured["env"]["HERMES_HOME"] == str(tmp_path)
        # Command pins the default profile: hermes -p default gateway run ...
        argv = captured["argv"]
        assert argv[0] == "/bin/echo"
        assert argv[1:5] == ["-p", "default", "gateway", "run"]
        # No process-level CRM_USER_ID leak (identity is per-turn under B1).
        assert "CRM_USER_ID" not in captured["env"]
        # Shared listener env still set.
        assert captured["env"]["API_SERVER_PORT"] == "9100"
        assert captured["env"]["GATEWAY_ALLOW_ALL_USERS"] == "true"
    finally:
        if log_file:
            log_file.close()
