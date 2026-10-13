from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from hermes_cloud.app.profile_manager import ProfileManager


@pytest.fixture(autouse=True)
def _non_multiplex_topology(monkeypatch):
    """This module exercises the per-profile (one gateway per profile)
    lifecycle. Since 2026-07-24 multiplex is the DEFAULT topology, so these
    tests must explicitly opt out to keep asserting per-profile behavior
    (distinct entries per profile, per-profile stop/respawn, …)."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")


@pytest.mark.asyncio
async def test_launch_process_clears_stale_gateway_state_for_dead_pid(tmp_path):
    profile_id = "user-1"
    profile_dir = tmp_path / "profiles" / profile_id
    profile_dir.mkdir(parents=True)
    state_path = profile_dir / "gateway_state.json"
    state_path.write_text(json.dumps({
        "pid": 999999,
        "gateway_state": "stopped",
    }))

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )

    class DummyProcess:
        pid = 12345
        returncode = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        return DummyProcess()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        process, log_file = await manager._launch_process(
            profile_id, 9100, user_id=profile_id
        )

    try:
        assert process.pid == 12345
        assert state_path.exists() is False
    finally:
        log_file.close()


@pytest.mark.asyncio
async def test_two_profiles_same_user_use_different_entries(tmp_path):
    """A user with multiple profiles: each profile gets its own _ProcessEntry,
    its own port, its own HERMES_HOME directory."""

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
        port_range_start=9100,
        port_range_end=9101,
    )

    class DummyProcess:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None

    pids = iter([10001, 10002])
    captured_envs = []

    async def fake_create_subprocess_exec(*args, env=None, **kwargs):
        captured_envs.append(env)
        return DummyProcess(next(pids))

    async def fake_wait_healthy(port, timeout=30.0):
        return True

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        with patch.object(manager, "_wait_healthy", side_effect=fake_wait_healthy):
            port_a = await manager.ensure_running("alice", user_id="alice")
            port_b = await manager.ensure_running("alice-research", user_id="alice")

    assert port_a != port_b
    # Both subprocesses belong to the same user but different profiles
    entries = manager.get_running_processes()
    profile_ids = {e["profile_id"] for e in entries}
    user_ids = {e["user_id"] for e in entries}
    assert profile_ids == {"alice", "alice-research"}
    assert user_ids == {"alice"}
    # HERMES_HOME env vars point at distinct profile dirs
    homes = {env["HERMES_HOME"] for env in captured_envs}
    assert homes == {
        str(tmp_path / "profiles" / "alice"),
        str(tmp_path / "profiles" / "alice-research"),
    }


@pytest.mark.asyncio
async def test_stop_user_stops_all_profiles_of_that_user(tmp_path):
    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
        port_range_start=9100,
        port_range_end=9102,
    )

    class DummyProcess:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        return DummyProcess(pid=10001)

    async def fake_wait_healthy(port, timeout=30.0):
        return True

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        with patch.object(manager, "_wait_healthy", side_effect=fake_wait_healthy):
            await manager.ensure_running("alice", user_id="alice")
            await manager.ensure_running("alice-research", user_id="alice")
            await manager.ensure_running("bob", user_id="bob")

    await manager.stop_user("alice")
    remaining_profile_ids = {e["profile_id"] for e in manager.get_running_processes()}
    assert remaining_profile_ids == {"bob"}


@pytest.mark.asyncio
async def test_stop_profile_stops_one_entry(tmp_path):
    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
        port_range_start=9100,
        port_range_end=9101,
    )

    class DummyProcess:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        return DummyProcess(pid=10001)

    async def fake_wait_healthy(port, timeout=30.0):
        return True

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        with patch.object(manager, "_wait_healthy", side_effect=fake_wait_healthy):
            await manager.ensure_running("alice", user_id="alice")
            await manager.ensure_running("alice-research", user_id="alice")

    await manager.stop_profile("alice-research")
    remaining = {e["profile_id"] for e in manager.get_running_processes()}
    assert remaining == {"alice"}


def test_configure_profile_backfills_legacy_model_default(tmp_path):
    """Legacy configs wrote `model: {model: xxx}` without `default`; cron
    reads `model.default` and would see None. _configure_profile should
    copy `model` → `default` when `default` is missing."""

    profile_id = "user-legacy"
    profile_dir = tmp_path / "profiles" / profile_id
    profile_dir.mkdir(parents=True)
    config_file = profile_dir / "config.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "model": {
                    "model": "big-pickle",
                    "base_url": "https://opencode.ai/zen/v1",
                    "provider": "custom",
                }
            }
        )
    )

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )
    manager._configure_profile(profile_id, 9100)

    written = yaml.safe_load(config_file.read_text())
    assert written["model"]["default"] == "big-pickle"
    assert written["model"]["model"] == "big-pickle"


def test_configure_profile_preserves_existing_default(tmp_path):
    """If `default` is already set, don't overwrite it with `model`."""

    profile_id = "user-ok"
    profile_dir = tmp_path / "profiles" / profile_id
    profile_dir.mkdir(parents=True)
    config_file = profile_dir / "config.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "model": {
                    "default": "correct-model",
                    "model": "stale-model",
                }
            }
        )
    )

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )
    manager._configure_profile(profile_id, 9100)

    written = yaml.safe_load(config_file.read_text())
    assert written["model"]["default"] == "correct-model"


# ══════════════════════════════════════════════════════════════════════════
# ②a scale-to-zero + drain
# ══════════════════════════════════════════════════════════════════════════

from hermes_cloud.app.profile_manager import (  # noqa: E402
    _ProcessEntry,
    cloud_idle_timeout_seconds,
    cloud_scale_to_zero_enabled,
)


def test_cloud_scale_to_zero_enabled_reads_env():
    assert cloud_scale_to_zero_enabled({}) is False
    assert cloud_scale_to_zero_enabled({"HERMES_CLOUD_SCALE_TO_ZERO": ""}) is False
    assert cloud_scale_to_zero_enabled({"HERMES_CLOUD_SCALE_TO_ZERO": "0"}) is False
    assert cloud_scale_to_zero_enabled({"HERMES_CLOUD_SCALE_TO_ZERO": "1"}) is True
    assert cloud_scale_to_zero_enabled({"HERMES_CLOUD_SCALE_TO_ZERO": "true"}) is True
    assert cloud_scale_to_zero_enabled({"HERMES_CLOUD_SCALE_TO_ZERO": "ON"}) is True


def test_cloud_idle_timeout_seconds_default_and_override():
    # default 5 min
    assert cloud_idle_timeout_seconds({}) == 5 * 60.0
    assert cloud_idle_timeout_seconds({"HERMES_CLOUD_IDLE_TIMEOUT_MINUTES": "10"}) == 600.0
    # 非数值 / 非正 → 回落默认(绝不返回 <= 0,否则刚起就被回收)
    assert cloud_idle_timeout_seconds({"HERMES_CLOUD_IDLE_TIMEOUT_MINUTES": "0"}) == 300.0
    assert cloud_idle_timeout_seconds({"HERMES_CLOUD_IDLE_TIMEOUT_MINUTES": "-3"}) == 300.0
    assert cloud_idle_timeout_seconds({"HERMES_CLOUD_IDLE_TIMEOUT_MINUTES": "junk"}) == 300.0


def test_configure_profile_seeds_drain_and_scale_to_zero(tmp_path):
    profile_id = "user-stz"
    profile_dir = tmp_path / "profiles" / profile_id
    profile_dir.mkdir(parents=True)
    config_file = profile_dir / "config.yaml"

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )
    manager._configure_profile(profile_id, 9100)

    written = yaml.safe_load(config_file.read_text())
    # SIGTERM 优雅 drain 预算被种下
    assert written["agent"]["restart_drain_timeout"] == 30.0
    # 前向兼容的 scale_to_zero 行为配置被种下
    assert written["scale_to_zero"]["idle_timeout_minutes"] == 5


def test_configure_profile_drain_seed_is_seed_only(tmp_path):
    """用户/UI 显式设过 restart_drain_timeout / scale_to_zero 时不被覆盖。"""
    profile_id = "user-keep"
    profile_dir = tmp_path / "profiles" / profile_id
    profile_dir.mkdir(parents=True)
    config_file = profile_dir / "config.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "agent": {"restart_drain_timeout": 90},
                "scale_to_zero": {"idle_timeout_minutes": 15},
            }
        )
    )

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )
    manager._configure_profile(profile_id, 9100)

    written = yaml.safe_load(config_file.read_text())
    assert written["agent"]["restart_drain_timeout"] == 90
    assert written["scale_to_zero"]["idle_timeout_minutes"] == 15


@pytest.mark.asyncio
async def test_launch_process_stamps_scale_to_zero_env_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_SCALE_TO_ZERO", "1")
    profile_id = "user-env"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )

    class DummyProcess:
        pid = 4242
        returncode = None

    captured = {}

    async def fake_exec(*args, env=None, **kwargs):
        captured["env"] = env
        return DummyProcess()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        _, log_file = await manager._launch_process(profile_id, 9100, user_id=profile_id)
    try:
        assert captured["env"]["HERMES_SCALE_TO_ZERO"] == "1"
    finally:
        log_file.close()


@pytest.mark.asyncio
async def test_launch_process_no_scale_to_zero_env_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_CLOUD_SCALE_TO_ZERO", raising=False)
    profile_id = "user-noenv"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )

    class DummyProcess:
        pid = 4243
        returncode = None

    captured = {}

    async def fake_exec(*args, env=None, **kwargs):
        captured["env"] = env
        return DummyProcess()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        _, log_file = await manager._launch_process(profile_id, 9100, user_id=profile_id)
    try:
        assert "HERMES_SCALE_TO_ZERO" not in captured["env"]
    finally:
        log_file.close()


def _make_manager_with_entry(tmp_path, profile_id, *, idle_seconds, adopted=False):
    """构造一个带单条 running entry 的 manager,便于 reaper 测试。"""
    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )

    class DummyProcess:
        def __init__(self):
            self.pid = 55555
            self.returncode = None
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True
            self.returncode = 0  # 干净退出

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    proc = None if adopted else DummyProcess()
    entry = _ProcessEntry(
        profile_id=profile_id,
        user_id=profile_id,
        port=9100,
        process=proc,
        pid=(55555 if adopted else None),
    )
    # 把 last_active 往回拨,伪造空闲
    entry.last_active = entry.last_active - idle_seconds
    manager._processes[profile_id] = entry
    manager._free_ports.discard(9100)
    return manager, entry, proc


@pytest.mark.asyncio
async def test_reaper_sleeps_idle_non_cron_non_busy_profile(tmp_path):
    profile_id = "idle-one"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)
    manager, entry, proc = _make_manager_with_entry(
        tmp_path, profile_id, idle_seconds=10_000
    )

    async def not_busy(port):
        return False

    with patch.object(manager, "_gateway_is_busy", side_effect=not_busy):
        await manager._reap_idle_once()

    # 进程被 SIGTERM(terminate)且从注册表移除 → 已休眠
    assert proc.terminated is True
    assert profile_id not in manager._processes
    assert 9100 in manager._free_ports


@pytest.mark.asyncio
async def test_reaper_skips_profile_with_enabled_crons(tmp_path):
    profile_id = "cron-one"
    cron_dir = tmp_path / "profiles" / profile_id / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "jobs.json").write_text(
        json.dumps({"jobs": [{"enabled": True, "id": "daily"}]})
    )
    manager, entry, proc = _make_manager_with_entry(
        tmp_path, profile_id, idle_seconds=10_000
    )

    async def not_busy(port):
        return False

    with patch.object(manager, "_gateway_is_busy", side_effect=not_busy):
        await manager._reap_idle_once()

    # 有 cron → 必须常驻,不休眠
    assert proc.terminated is False
    assert profile_id in manager._processes


@pytest.mark.asyncio
async def test_reaper_skips_busy_gateway(tmp_path):
    profile_id = "busy-one"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)
    manager, entry, proc = _make_manager_with_entry(
        tmp_path, profile_id, idle_seconds=10_000
    )

    async def busy(port):
        return True

    with patch.object(manager, "_gateway_is_busy", side_effect=busy):
        await manager._reap_idle_once()

    # 有在途回合 → 不休眠,且 idle 计时器被摸新
    assert proc.terminated is False
    assert profile_id in manager._processes
    assert entry.idle_seconds() < 5.0


@pytest.mark.asyncio
async def test_reaper_skips_when_health_probe_fails(tmp_path):
    profile_id = "unknown-one"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)
    manager, entry, proc = _make_manager_with_entry(
        tmp_path, profile_id, idle_seconds=10_000
    )

    async def probe_failed(port):
        return None  # 探测失败:不确定是否忙

    with patch.object(manager, "_gateway_is_busy", side_effect=probe_failed):
        await manager._reap_idle_once()

    # 不确定 → 保守不休眠
    assert proc.terminated is False
    assert profile_id in manager._processes


@pytest.mark.asyncio
async def test_reaper_skips_not_yet_idle_profile(tmp_path):
    profile_id = "fresh-one"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)
    # 空闲仅 10s,远小于 5min 阈值
    manager, entry, proc = _make_manager_with_entry(
        tmp_path, profile_id, idle_seconds=10
    )

    called = {"probe": False}

    async def not_busy(port):
        called["probe"] = True
        return False

    with patch.object(manager, "_gateway_is_busy", side_effect=not_busy):
        await manager._reap_idle_once()

    assert proc.terminated is False
    assert profile_id in manager._processes
    # 未到阈值,连 health 探测都不该发起
    assert called["probe"] is False


@pytest.mark.asyncio
async def test_reaper_skips_adopted_entries(tmp_path):
    """adopted 条目(process=None、pid set)是跨云重启存活的 IM 长连接子进程,
    不归 reaper 生命周期管——绝不 SIGTERM。"""
    profile_id = "adopted-one"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)
    manager, entry, proc = _make_manager_with_entry(
        tmp_path, profile_id, idle_seconds=10_000, adopted=True
    )

    probe_called = {"v": False}

    async def not_busy(port):
        probe_called["v"] = True
        return False

    with patch.object(manager, "_gateway_is_busy", side_effect=not_busy):
        # 让 is_running(走 os.kill(pid,0))返回 True
        with patch("os.kill", return_value=None):
            await manager._reap_idle_once()

    assert profile_id in manager._processes
    assert probe_called["v"] is False


@pytest.mark.asyncio
async def test_died_process_is_respawned_not_marked_failed(tmp_path):
    """核心正确性:干净休眠退出(returncode 非 None)被 _ensure_running_locked
    当作正常「重起一个」,没有退避、不标记失败 → 唤醒可用。"""
    profile_id = "wake-one"
    (tmp_path / "profiles" / profile_id).mkdir(parents=True)

    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )

    # 先塞一个「已退出」的 entry(模拟被 reaper 休眠后的状态)
    class DeadProcess:
        pid = 111
        returncode = 0  # 干净退出

    manager._processes[profile_id] = _ProcessEntry(
        profile_id=profile_id, user_id=profile_id, port=9100,
        process=DeadProcess(),
    )
    manager._free_ports.discard(9100)

    class FreshProcess:
        pid = 222
        returncode = None

    async def fake_exec(*args, env=None, **kwargs):
        return FreshProcess()

    async def fake_healthy(port, timeout=30.0):
        return True

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with patch.object(manager, "_wait_healthy", side_effect=fake_healthy):
            port = await manager.ensure_running(profile_id, user_id=profile_id)

    # 唤醒成功:重生了一个新进程,profile 未被标记失败
    assert isinstance(port, int)
    assert manager._processes[profile_id].process.pid == 222


@pytest.mark.asyncio
async def test_reaper_skips_profile_with_bound_im_channel(tmp_path):
    """绑了持久入站 IM 渠道(Feishu WS 等)的 profile 必须常驻——休眠会断长连接、
    丢入站(charter §一系统)。"""
    profile_id = "feishu-one"
    profile_dir = tmp_path / "profiles" / profile_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        yaml.dump(
            {
                "platforms": {
                    "feishu": {"extra": {"app_id": "cli_x", "app_secret": "s"}},
                }
            }
        )
    )
    manager, entry, proc = _make_manager_with_entry(
        tmp_path, profile_id, idle_seconds=10_000
    )

    probe_called = {"v": False}

    async def not_busy(port):
        probe_called["v"] = True
        return False

    with patch.object(manager, "_gateway_is_busy", side_effect=not_busy):
        await manager._reap_idle_once()

    assert proc.terminated is False
    assert profile_id in manager._processes
    # 绑了长连接渠道,连 health 探测都不该发起(早在候选阶段就被排除)
    assert probe_called["v"] is False


def test_has_persistent_inbound_channel_detects_ws_channels(tmp_path):
    manager = ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )

    def _write(profile_id, cfg):
        d = tmp_path / "profiles" / profile_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.yaml").write_text(yaml.dump(cfg))

    # 无 config → False
    (tmp_path / "profiles" / "none").mkdir(parents=True)
    assert manager._profile_has_persistent_inbound_channel("none") is False

    # 有 feishu → True
    _write("fs", {"platforms": {"feishu": {"extra": {"app_id": "x"}}}})
    assert manager._profile_has_persistent_inbound_channel("fs") is True

    # 有 dingtalk → True
    _write("dt", {"platforms": {"dingtalk": {"extra": {"client_id": "x"}}}})
    assert manager._profile_has_persistent_inbound_channel("dt") is True

    # 仅纯 webhook 渠道(email)且无 WS 渠道 → False(email 由云端路由)
    _write("em", {"platforms": {"email": {"extra": {"addr": "a@b.c"}}}})
    assert manager._profile_has_persistent_inbound_channel("em") is False

    # 空 platforms → False
    _write("empty", {"platforms": {}})
    assert manager._profile_has_persistent_inbound_channel("empty") is False
