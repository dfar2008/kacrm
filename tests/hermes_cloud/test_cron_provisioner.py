"""cron_provisioner must decide "profile has a channel" from config.yaml.

Regression: the old channel_bindings model stored per-profile channel
state in ``channels.json``. After that model was removed (per-user-bot
refactor) the file no longer exists — but ``_profile_has_active_channel``
still checked it, so every profile looked channel-less and
``ensure_default_jobs`` wiped the provisioned daily-coach /
weekly-digest jobs from ``cron/jobs.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from hermes_cloud.app.cron_provisioner import (
    DEFAULT_JOBS,
    _profile_has_active_channel,
    ensure_default_jobs,
)

_DEFAULT_NAMES = {s["name"] for s in DEFAULT_JOBS}


def _write_config(profile_dir: Path, platforms: dict) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump({"platforms": platforms}), encoding="utf-8",
    )


def _jobs(hermes_home: Path, profile_id: str) -> set[str]:
    path = hermes_home / "profiles" / profile_id / "cron" / "jobs.json"
    return {j["name"] for j in json.loads(path.read_text())["jobs"]}


def test_active_channel_true_when_config_has_enabled_platform(tmp_path):
    _write_config(
        tmp_path / "profiles" / "p1",
        {"feishu": {"enabled": True, "extra": {"app_id": "x"}}},
    )
    assert _profile_has_active_channel(tmp_path, "p1") is True


def test_active_channel_false_when_no_config(tmp_path):
    (tmp_path / "profiles" / "p1").mkdir(parents=True)
    assert _profile_has_active_channel(tmp_path, "p1") is False


def test_active_channel_false_when_platforms_empty(tmp_path):
    _write_config(tmp_path / "profiles" / "p1", {})
    assert _profile_has_active_channel(tmp_path, "p1") is False


def test_ensure_default_jobs_adds_defaults_for_profile_with_channel(tmp_path):
    _write_config(
        tmp_path / "profiles" / "p1",
        {"weixin": {"enabled": True, "extra": {"account_id": "a@im.bot"}}},
    )
    res = ensure_default_jobs("p1", tmp_path, user_id=42)
    assert set(res["added"]) == _DEFAULT_NAMES
    assert _jobs(tmp_path, "p1") == _DEFAULT_NAMES


def test_ensure_default_jobs_keeps_jobs_across_restart(tmp_path):
    """The regression: a profile with config.yaml channels and no
    channels.json must keep its provisioned jobs across a re-run
    (cloud restart), not have them wiped."""
    _write_config(
        tmp_path / "profiles" / "p1",
        {"feishu": {"enabled": True, "extra": {"app_id": "x"}}},
    )
    ensure_default_jobs("p1", tmp_path, user_id=42)        # first boot
    res = ensure_default_jobs("p1", tmp_path, user_id=42)  # restart: keep
    assert res["skipped"] is None
    assert set(res["kept"]) == _DEFAULT_NAMES
    assert _jobs(tmp_path, "p1") == _DEFAULT_NAMES


def test_ensure_default_jobs_skips_profile_without_channel(tmp_path):
    _write_config(tmp_path / "profiles" / "p1", {})
    res = ensure_default_jobs("p1", tmp_path)
    assert res["skipped"] == "no_active_channels"


def _provisioned_job(hermes_home: Path, profile_id: str, name: str) -> dict:
    path = hermes_home / "profiles" / profile_id / "cron" / "jobs.json"
    for j in json.loads(path.read_text())["jobs"]:
        if j["name"] == name:
            return j
    raise AssertionError(f"job {name} not found")


def test_daily_coach_job_has_deliver_render_and_user_id(tmp_path):
    _write_config(
        tmp_path / "profiles" / "p1",
        {"feishu": {"enabled": True, "extra": {"app_id": "x"}}},
    )
    ensure_default_jobs("p1", tmp_path, user_id=42)
    coach = _provisioned_job(tmp_path, "p1", "crm-daily-coach")
    assert coach["deliver_render"] == "coach_digest"
    assert coach["user_id"] == 42


def test_weekly_digest_job_has_no_deliver_render(tmp_path):
    _write_config(
        tmp_path / "profiles" / "p1",
        {"feishu": {"enabled": True, "extra": {"app_id": "x"}}},
    )
    ensure_default_jobs("p1", tmp_path, user_id=42)
    weekly = _provisioned_job(tmp_path, "p1", "crm-weekly-digest")
    assert weekly["deliver_render"] is None


def test_coach_job_skipped_when_user_id_unresolved(tmp_path):
    """A coach-digest job is non-functional without a cloud user_id, so
    it must NOT be provisioned when user_id can't be resolved — but the
    user-id-independent weekly digest still is."""
    _write_config(
        tmp_path / "profiles" / "p1",
        {"feishu": {"enabled": True, "extra": {"app_id": "x"}}},
    )
    res = ensure_default_jobs("p1", tmp_path, user_id=None)
    assert res["added"] == ["crm-weekly-digest"]
    assert _jobs(tmp_path, "p1") == {"crm-weekly-digest"}


def test_coach_job_provisioned_once_user_id_resolves(tmp_path):
    """A later backfill (after the profile gains a user_id) provisions
    all jobs that require a user_id and were skipped on the first pass."""
    _write_config(
        tmp_path / "profiles" / "p1",
        {"feishu": {"enabled": True, "extra": {"app_id": "x"}}},
    )
    _user_id_required = {s["name"] for s in DEFAULT_JOBS if s.get("requires_user_id")}
    ensure_default_jobs("p1", tmp_path, user_id=None)   # skips all requires_user_id jobs
    res = ensure_default_jobs("p1", tmp_path, user_id=42)
    assert set(res["added"]) == _user_id_required
    assert _jobs(tmp_path, "p1") == _DEFAULT_NAMES
    coach = _provisioned_job(tmp_path, "p1", "crm-daily-coach")
    assert coach["user_id"] == 42


def test_drift_backfills_deliver_render_and_user_id(tmp_path):
    """An old provisioned job lacking the new fields is reconciled."""
    _write_config(
        tmp_path / "profiles" / "p1",
        {"feishu": {"enabled": True, "extra": {"app_id": "x"}}},
    )
    ensure_default_jobs("p1", tmp_path, user_id=42)
    # Simulate an old job: strip the new fields back out.
    path = tmp_path / "profiles" / "p1" / "cron" / "jobs.json"
    doc = json.loads(path.read_text())
    for j in doc["jobs"]:
        if j["name"] == "crm-daily-coach":
            j.pop("deliver_render", None)
            j["user_id"] = None
    path.write_text(json.dumps(doc))

    ensure_default_jobs("p1", tmp_path, user_id=42)
    coach = _provisioned_job(tmp_path, "p1", "crm-daily-coach")
    assert coach["deliver_render"] == "coach_digest"
    assert coach["user_id"] == 42
