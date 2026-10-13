"""Review-finding fixes for ProfileManager multiplex topology.

Covers four real bugs found in review, all gated on HERMES_CLOUD_MULTIPLEX so
flag-off behavior is byte-for-byte unchanged (regression covered by
test_profile_manager_multiplex.py + test_profile_manager.py):

  R1-1  stop_user must NEVER stop the shared __mux__ gateway.
  R1-2  the resident keepalive loop must revive a dead mux (flag on only).
  R1-M1 _ensure_mux_running must persist the mux registry (so adoption works).
  R1-M3 adoption is flag-gated: flag-on adopts only __mux__ (and evicts stale
        per-profile survivors); flag-off adopts only per-profile.
  R1-M2 provider keys are materialized into each profile's OWN .env (never
        os.environ) so the shared mux can resolve them per-turn.

No real subprocess / gateway is started — spawn + health + os.kill are mocked.
"""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest
import yaml

from hermes_cloud.app.profile_manager import ProfileManager, _ProcessEntry


def _manager(tmp_path):
    return ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
        port_range_start=9100,
        port_range_end=9110,
    )


class _Dummy:
    def __init__(self, pid=1234):
        self.pid = pid
        self.returncode = None


def _write_registry(root, profile_id, *, pid, port, user_id="U"):
    d = root / "profiles" / profile_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "cloud_subprocess.json").write_text(
        json.dumps(
            {"profile_id": profile_id, "user_id": user_id, "pid": pid, "port": port}
        )
    )


# ── R1-1: stop_user never kills the shared mux ──────────────────────────────

@pytest.mark.asyncio
async def test_stop_user_never_stops_mux(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)
    # mux entry carries the user_id of the tenant that first woke it.
    manager._processes[manager._MUX_KEY] = _ProcessEntry(
        profile_id=manager._MUX_KEY, user_id="U", port=9100, process=_Dummy(),
    )
    manager._processes["alice"] = _ProcessEntry(
        profile_id="alice", user_id="U", port=9101, process=_Dummy(),
    )

    stopped: list[str] = []

    async def fake_stop(pid):
        stopped.append(pid)

    monkeypatch.setattr(manager, "stop_profile", fake_stop)
    await manager.stop_user("U")

    assert "alice" in stopped
    assert manager._MUX_KEY not in stopped  # would take EVERY tenant offline


# ── R1-2: keepalive revives dead mux (gated) ────────────────────────────────

@pytest.mark.asyncio
async def test_keepalive_revives_mux_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    monkeypatch.setenv("HERMES_CLOUD_KEEPALIVE_INTERVAL", "0")
    manager = _manager(tmp_path)

    calls: list[str] = []

    async def fake_ensure_mux(user_id):
        calls.append(user_id)
        return 9100

    monkeypatch.setattr(manager, "_ensure_mux_running", fake_ensure_mux)

    task = asyncio.create_task(manager._resident_keepalive_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(calls) >= 1  # mux ensured at least once per tick


@pytest.mark.asyncio
async def test_keepalive_does_not_touch_mux_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")  # 显式 opt-out
    monkeypatch.setenv("HERMES_CLOUD_KEEPALIVE_INTERVAL", "0")
    manager = _manager(tmp_path)

    calls: list[str] = []

    async def fake_ensure_mux(user_id):
        calls.append(user_id)
        return 9100

    monkeypatch.setattr(manager, "_ensure_mux_running", fake_ensure_mux)

    task = asyncio.create_task(manager._resident_keepalive_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert calls == []  # per-profile path only; never touches the mux


# ── R1-M1: mux registry is persisted ────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_mux_persists_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)

    async def fake_launch(port, user_id=None):
        return _Dummy(pid=7777), None

    with patch.object(manager, "_launch_mux_process", side_effect=fake_launch), \
        patch.object(manager, "_wait_healthy", return_value=True):
        port = await manager._ensure_mux_running("alice")

    reg = tmp_path / "profiles" / manager._MUX_KEY / "cloud_subprocess.json"
    assert reg.exists()
    data = json.loads(reg.read_text())
    assert data["profile_id"] == manager._MUX_KEY
    assert data["pid"] == 7777
    assert data["port"] == port


# ── R1-M3: flag-gated adoption ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adoption_flag_on_adopts_only_mux_and_evicts_per_profile(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    manager = _manager(tmp_path)
    _write_registry(tmp_path, "alice", pid=101, port=9101)
    _write_registry(tmp_path, manager._MUX_KEY, pid=102, port=9100)

    kills: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))  # sig 0 (liveness) → pretend alive: no raise

    monkeypatch.setattr(
        "hermes_cloud.app.profile_manager.os.kill", fake_kill
    )

    with patch.object(manager, "_wait_healthy", return_value=True):
        adopted = await manager._adopt_existing_subprocesses()

    assert manager._MUX_KEY in manager._processes
    assert "alice" not in manager._processes
    assert (101, 15) in kills  # stale per-profile survivor SIGTERM'd
    assert not (
        tmp_path / "profiles" / "alice" / "cloud_subprocess.json"
    ).exists()
    assert (
        tmp_path / "profiles" / manager._MUX_KEY / "cloud_subprocess.json"
    ).exists()  # mux registry preserved (adopted)
    assert adopted == 1


@pytest.mark.asyncio
async def test_adoption_flag_off_adopts_only_per_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")  # 显式 opt-out
    manager = _manager(tmp_path)
    _write_registry(tmp_path, "alice", pid=101, port=9101)
    _write_registry(tmp_path, manager._MUX_KEY, pid=102, port=9100)

    kills: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))

    monkeypatch.setattr(
        "hermes_cloud.app.profile_manager.os.kill", fake_kill
    )

    with patch.object(manager, "_wait_healthy", return_value=True):
        adopted = await manager._adopt_existing_subprocesses()

    assert "alice" in manager._processes
    assert manager._MUX_KEY not in manager._processes
    assert (102, 15) not in kills  # mux left untouched, not SIGTERM'd
    assert adopted == 1


# ── R1-M2: provider keys materialized to per-profile .env, not os.environ ────

def test_materialize_provider_key_writes_env_not_os_environ(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    profile_dir = tmp_path / "profiles" / "alice"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        yaml.dump({"model": {"provider": "opencode-zen", "api_key": "sk-secret-123"}})
    )
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)

    manager._materialize_provider_keys_to_env(profile_dir)

    env_text = (profile_dir / ".env").read_text()
    assert "OPENCODE_ZEN_API_KEY=sk-secret-123" in env_text
    # CRITICAL: never leak into the shared process env (cross-tenant poison).
    assert "OPENCODE_ZEN_API_KEY" not in os.environ


def test_materialize_no_provider_key_writes_only_api_server_key(tmp_path):
    """A profile with no mapped PROVIDER key still gets its .env seeded with
    API_SERVER_KEY — and nothing else.

    Contract change (2026-08-16, multiplex live fix): hermes v0.20.1 resolves
    a named profile's API_SERVER_KEY from that profile's own scope and FAILS
    CLOSED when none exists, so every /p/{tenant}/… call 401'd until the
    materializer seeded the key. Writing it is therefore no longer optional —
    what stays conditional is the provider key.
    """
    manager = _manager(tmp_path)
    profile_dir = tmp_path / "profiles" / "bob"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        yaml.dump({"model": {"provider": "custom"}})
    )
    manager._materialize_provider_keys_to_env(profile_dir)
    env_text = (profile_dir / ".env").read_text()
    assert "API_SERVER_KEY=" in env_text
    assert "OPENCODE_ZEN_API_KEY" not in env_text


def test_materialize_seeds_authz_gate_into_profile_scope(tmp_path):
    """The IM authorization gate must land in the profile's OWN .env.

    Regression (live 2026-09-04): gateway/authz_mixin reads
    GATEWAY_ALLOW_ALL_USERS from the per-profile secret SCOPE under
    multiplex and returns the default on a scope miss (never falling
    through to os.environ — that isolation is deliberate, #72348). The mux
    process env sets the flag, but once a profile .env exists a scope is
    installed, so an absent key read as "not allow-all" and every Feishu /
    WeChat user got "Hi~ I don't recognize you yet" + a pairing code.
    Seeding API_SERVER_KEY (2026-08-16) created that .env for profiles that
    previously had none, which is what flipped the gate.
    """
    manager = _manager(tmp_path)
    profile_dir = tmp_path / "profiles" / "dora"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(yaml.dump({"model": {}}))
    manager._materialize_provider_keys_to_env(profile_dir)
    env_text = (profile_dir / ".env").read_text()
    assert "GATEWAY_ALLOW_ALL_USERS=" in env_text
    assert "true" in env_text.split("GATEWAY_ALLOW_ALL_USERS=")[1].splitlines()[0]


def test_materialize_all_provider_keys_covers_every_profile(tmp_path):
    manager = _manager(tmp_path)
    for name in ("alice", "carol"):
        d = tmp_path / "profiles" / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(
            yaml.dump(
                {"model": {"provider": "opencode-zen", "api_key": f"key-{name}"}}
            )
        )
    manager._materialize_all_provider_keys()
    assert "OPENCODE_ZEN_API_KEY=key-alice" in (
        tmp_path / "profiles" / "alice" / ".env"
    ).read_text()
    assert "OPENCODE_ZEN_API_KEY=key-carol" in (
        tmp_path / "profiles" / "carol" / ".env"
    ).read_text()


def test_upsert_env_var_replaces_and_preserves_other_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO=bar\nOPENCODE_ZEN_API_KEY=old\nBAZ=qux\n")
    ProfileManager._upsert_env_var(p, "OPENCODE_ZEN_API_KEY", "new")
    lines = p.read_text().splitlines()
    assert "FOO=bar" in lines
    assert "BAZ=qux" in lines
    assert "OPENCODE_ZEN_API_KEY=new" in lines
    assert "OPENCODE_ZEN_API_KEY=old" not in lines
