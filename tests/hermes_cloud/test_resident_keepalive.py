"""Resident keepalive regressions (2026-07-07 feishu-deaf incident).

A profile whose gateway holds a persistent IM WebSocket (feishu/dingtalk/…)
or a cron ticker must stay resident: the boot eager-spawn covers startup,
and _resident_keepalive_loop revives it if the process dies mid-flight —
nothing else can, because inbound IM arrives via the (dead) in-process
listener, never via cloud HTTP dispatch.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from hermes_cloud.app.profile_manager import ProfileManager


@pytest.fixture(autouse=True)
def _non_multiplex_topology(monkeypatch):
    """Resident keepalive is a per-profile (non-multiplex) concern. Multiplex
    became the DEFAULT topology 2026-07-24, so opt out explicitly here."""
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")


def _mk_manager(tmp_path):
    return ProfileManager(
        internal_key="dev-internal-key",
        hermes_bin="/bin/echo",
        hermes_home_root=tmp_path,
    )


def _mk_profile(tmp_path, profile_id, *, feishu=False, cron=False):
    d = tmp_path / "profiles" / profile_id
    d.mkdir(parents=True)
    cfg = {}
    if feishu:
        cfg["platforms"] = {"feishu": {"app_id": "cli_x", "app_secret": "s"}}
    (d / "config.yaml").write_text(
        json.dumps(cfg) if not cfg else __import__("yaml").safe_dump(cfg),
        encoding="utf-8",
    )
    if cron:
        (d / "cron").mkdir()
        (d / "cron" / "jobs.json").write_text(
            json.dumps({"jobs": [{"id": "j1", "enabled": True}]}),
            encoding="utf-8",
        )
    return d


# ── residency eligibility ────────────────────────────────────────────


def test_must_stay_resident_im_channel(tmp_path):
    m = _mk_manager(tmp_path)
    _mk_profile(tmp_path, "im-only", feishu=True)
    _mk_profile(tmp_path, "cron-only", cron=True)
    _mk_profile(tmp_path, "plain")
    assert m._profile_must_stay_resident("im-only")
    assert m._profile_must_stay_resident("cron-only")
    assert not m._profile_must_stay_resident("plain")


# ── keepalive loop behavior ──────────────────────────────────────────


async def _run_one_tick(manager, monkeypatch):
    """Run the keepalive loop for exactly one tick."""
    monkeypatch.setenv("HERMES_CLOUD_KEEPALIVE_INTERVAL", "0.01")
    task = asyncio.get_event_loop().create_task(
        manager._resident_keepalive_loop()
    )
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_keepalive_revives_dead_resident(tmp_path, monkeypatch):
    m = _mk_manager(tmp_path)
    _mk_profile(tmp_path, "im-bound", feishu=True)
    m.ensure_running = AsyncMock()
    await _run_one_tick(m, monkeypatch)
    assert m.ensure_running.await_count >= 1
    assert m.ensure_running.await_args.args[0] == "im-bound"


@pytest.mark.asyncio
async def test_keepalive_ignores_non_resident(tmp_path, monkeypatch):
    m = _mk_manager(tmp_path)
    _mk_profile(tmp_path, "plain")
    m.ensure_running = AsyncMock()
    await _run_one_tick(m, monkeypatch)
    assert m.ensure_running.await_count == 0


@pytest.mark.asyncio
async def test_keepalive_honors_manual_stop_grace(tmp_path, monkeypatch):
    m = _mk_manager(tmp_path)
    _mk_profile(tmp_path, "im-bound", feishu=True)
    m._manual_stops["im-bound"] = time.monotonic()  # just stopped
    m.ensure_running = AsyncMock()
    await _run_one_tick(m, monkeypatch)
    assert m.ensure_running.await_count == 0
    # …but after the grace window it revives again
    m._manual_stops["im-bound"] = (
        time.monotonic() - m._MANUAL_STOP_GRACE_SECONDS - 1
    )
    await _run_one_tick(m, monkeypatch)
    assert m.ensure_running.await_count >= 1


@pytest.mark.asyncio
async def test_keepalive_survives_respawn_failure(tmp_path, monkeypatch):
    m = _mk_manager(tmp_path)
    _mk_profile(tmp_path, "im-bound", feishu=True)
    m.ensure_running = AsyncMock(side_effect=RuntimeError("boom"))
    # Must not raise / kill the loop
    await _run_one_tick(m, monkeypatch)
    assert m.ensure_running.await_count >= 1
