"""get_running_processes must be multiplex-aware so the SPA shows each profile's
process port/PID instead of "○ 未运行".

Under multiplex there is ONE shared ``__mux__`` gateway serving every profile.
The admin/user UI looks each profile up by ``profile_id``; if the manager only
reported the ``__mux__`` sentinel, every real profile would render as stopped
even though it is being served. The fix fans the mux entry out to one row per
served profile (all sharing the mux port/pid), never leaking ``__mux__`` itself.
"""
from __future__ import annotations

import os
import tempfile
import time
import types
from pathlib import Path

import pytest

from hermes_cloud.app.profile_manager import ProfileManager, _ProcessEntry


def _pm_with_profiles(*names: str) -> tuple[ProfileManager, Path]:
    home = Path(tempfile.mkdtemp())
    for n in names:
        (home / "profiles" / n).mkdir(parents=True)
    pm = ProfileManager.__new__(ProfileManager)
    pm._hermes_home = home
    pm._user_id_resolver = None
    pm._processes = {}
    return pm, home


def test_multiplex_fans_mux_out_per_profile(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    pm, _ = _pm_with_profiles("admin", "wangz-914b")
    proc = types.SimpleNamespace(returncode=None, pid=94870)
    mux = _ProcessEntry(profile_id="__mux__", user_id="7", port=9500,
                        process=proc, log_file=None)
    mux.last_active = time.monotonic() - 5
    pm._processes = {ProfileManager._MUX_KEY: mux}

    rows = pm.get_running_processes()
    by_id = {r["profile_id"]: r for r in rows}

    # every served profile (default + on-disk) reports the shared mux port/pid
    assert "__mux__" not in by_id, "sentinel must never surface as a profile row"
    for name in ("default", "admin", "wangz-914b"):
        assert name in by_id, f"{name} should be reported as served"
        assert by_id[name]["running"] is True
        assert by_id[name]["port"] == 9500
        assert by_id[name]["pid"] == 94870
        assert by_id[name]["multiplexed"] is True
    # admin and wangz share the SAME process (the mux) — that's the point
    assert by_id["admin"]["port"] == by_id["wangz-914b"]["port"]
    assert by_id["admin"]["pid"] == by_id["wangz-914b"]["pid"]


def test_multiplex_mux_down_reports_empty(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "1")
    pm, _ = _pm_with_profiles("admin")
    # no __mux__ entry → nothing running
    assert pm.get_running_processes() == []


def test_non_multiplex_unchanged(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_MULTIPLEX", "0")  # 显式 opt-out(默认已是 multiplex)
    pm, _ = _pm_with_profiles("admin", "wangz-914b")
    p1 = types.SimpleNamespace(returncode=None, pid=111)
    p2 = types.SimpleNamespace(returncode=None, pid=222)
    e1 = _ProcessEntry(profile_id="admin", user_id="1", port=9100, process=p1, log_file=None)
    e2 = _ProcessEntry(profile_id="wangz-914b", user_id="2", port=9101, process=p2, log_file=None)
    pm._processes = {"admin": e1, "wangz-914b": e2}

    rows = pm.get_running_processes()
    by_id = {r["profile_id"]: r for r in rows}
    # per-profile: distinct ports/pids, no multiplexed flag (byte-for-byte legacy)
    assert by_id["admin"]["port"] == 9100 and by_id["admin"]["pid"] == 111
    assert by_id["wangz-914b"]["port"] == 9101 and by_id["wangz-914b"]["pid"] == 222
    assert "multiplexed" not in by_id["admin"]
    assert "default" not in by_id  # no synthetic default row when not multiplexing
