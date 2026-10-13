"""Provision default per-profile hermes cron jobs (native pattern).

Charter v2 §一 "agent is core, cloud is auxiliary": agent-driven
periodic surfaces (daily coach, weekly digest) are **native hermes
cron** jobs — hermes' built-in scheduler ticks every 60s, spawns the
main agent in-process with the configured skill, lets the agent run to
produce its final response, and delivers that response to every
listed home channel (``deliver="weixin,feishu"``). No shell scripts,
no HTTP self-call to cloud, no separate persist+push pipeline.

How cards land in ``gateway_actions``: the ``crm-daily-coach`` skill
itself calls the ``coach_proposal_submit(summary=, target=, action=,
severity=)`` tool (hermes-patches/49) during its run. Each submit
writes one ``gateway_actions`` row
(state=``pending_approval``) — same data flow the SPA reads from.
Delivery: ``crm-daily-coach`` carries ``deliver_render="coach_digest"``,
so the cron scheduler renders the wechat/feishu digest deterministically
from these cards (the agent's final response is NOT delivered).
``crm-weekly-digest`` has no ``deliver_render`` and still delivers the
agent's final response verbatim.

Feishu delivery is fully per-profile: each profile's hermes subprocess
runs its own native ``FeishuAdapter`` from that profile's config.yaml
``platforms.feishu`` credentials, and cron's ``deliver="feishu"``
delivers through that adapter. No cloud relay, no shared bot.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Canonical default jobs. Slug by ``name``; re-provisioning is idempotent.
DEFAULT_JOBS: list[dict[str, Any]] = [
    {
        "name": "crm-daily-coach",
        "schedule": "15 6 * * *",       # 06:15 local
        "skill": "crm-daily-coach",
        # Delivery is card-driven: the scheduler renders the IM digest
        # from the emitted coach.proposal cards (see scheduler
        # ``_DELIVER_RENDERERS``), not from the agent's free text.
        "deliver_render": "coach_digest",
        # The coach_digest renderer reads this user's coach proposals,
        # so the job is non-functional without a cloud user_id — gate
        # provisioning on it (see ensure_default_jobs).
        "requires_user_id": True,
        "prompt": (
            "今天是 daily coach cron。基于该 user 的 CRM 现状 + 过去 24h "
            "事件 + 最近 7 天 coach 反馈，按 crm-daily-coach skill 的契约，"
            "对每件今早最该提醒的事调 coach_proposal_submit(summary=..., "
            "context_line=..., target=..., action=..., severity=...) 持久化"
            "一张 card。card 的 summary / context_line / action.draft 必须"
            "自包含、可直接行动 —— 推送由系统从 card 确定性渲染，你的最终"
            "回复不会被投递。全部 card emit 完后，最终回复写一句话内部备注"
            "即可。没有该提醒的事 → emit 0 张 card（系统不推送）。"
        ),
        # Comma-separated platforms — equivalent to "all" in newer
        # hermes versions. Add more here as channels gain bindings.
        # (Older hermes v2026.5.7 doesn't support deliver="all" syntax.)
        "deliver": "weixin,feishu",
    },
    {
        "name": "crm-weekly-digest",
        "schedule": "0 9 * * mon",      # Monday 09:00 local
        "skill": "crm-weekly-digest",
        "prompt": (
            "今天是周一 09:00，本周 AI 详情 cron。基于过去 7 天的 CRM 活动，"
            "按 crm-weekly-digest skill 的契约写一段 200-400 字适合微信/飞书 "
            "发送的自然语言周报作为最终回复。无亮点 → 回复一句话简短摘要。"
        ),
        "deliver": "weixin,feishu",
    },
    {
        "name": "crm-draft-style-distill",
        "schedule": "0 23 * * sun",     # 每周日 23:00 local
        "skill": "crm-draft-style-distill",
        "requires_user_id": True,
        "prompt": (
            "今天是每周话术风格蒸馏 cron。按 crm-draft-style-distill skill "
            "的契约:调 crm_draft_feedback_collect 取近 14 天的「coach 草稿 ↔ "
            "结果」配对,有效信号 ≥3 条则 LLM 蒸馏出该销售的话术风格画像、用 "
            "memory 工具写进 USER.md 的「话术风格画像」条目;信号不足则跳过。"
            "这是静默后台 job,最终回复只写一句内部备注,不投递。"
        ),
        "deliver": "",
    },
]


def _profile_has_active_channel(hermes_home: Path, profile_id: str) -> bool:
    """True if the profile's config.yaml has at least one enabled channel.

    Under the per-user-bot model a profile's channel credentials live
    in its own ``config.yaml`` under ``platforms.<channel>`` with
    ``enabled: true`` — there is no longer a ``channels.json`` binding
    file. Skip provisioning for channel-less profiles: cron would fire,
    the agent would spend LLM budget, and ``deliver`` would resolve to
    nothing. Worse than not running.
    """
    from hermes_cloud.app.channel_config import read_profile_config

    profile_dir = Path(hermes_home) / "profiles" / profile_id
    platforms = read_profile_config(profile_dir).get("platforms")
    if not isinstance(platforms, dict):
        return False
    return any(
        isinstance(entry, dict) and entry.get("enabled")
        for entry in platforms.values()
    )


def _jobs_file(hermes_home: Path, profile_id: str) -> Path:
    return Path(hermes_home) / "profiles" / profile_id / "cron" / "jobs.json"


def _load_jobs(path: Path) -> dict:
    if not path.exists():
        return {"jobs": [], "updated_at": _now_iso()}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "jobs" not in data:
            return {"jobs": [], "updated_at": _now_iso()}
        return data
    except Exception:
        logger.exception("cron_provisioner: read failed %s", path)
        return {"jobs": [], "updated_at": _now_iso()}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_profile_user_id(profile_id: str) -> int | None:
    """Resolve a profile's cloud user_id (bigint) for cron job storage.

    Returns ``None`` on any failure — drift reconciliation only writes
    ``user_id`` when a real value is resolved, so a transient DB miss
    never wipes a previously-stored id.
    """
    try:
        from hermes_cloud.app.crm_profile_store import CrmProfileStore

        profile = CrmProfileStore().get_profile(profile_id)
        if profile and profile.user_id:
            return int(profile.user_id)
        # Profile resolved but carries no cloud user_id — a genuine
        # misconfiguration, not a transient miss. Log it explicitly so
        # it doesn't fail silently (the exception path below covers DB
        # outages; this covers the "exists but unlinked" case).
        logger.warning(
            "cron_provisioner: profile=%s has no cloud user_id — "
            "coach-digest cron will not be provisioned",
            profile_id,
        )
    except Exception:
        logger.warning(
            "cron_provisioner: user_id resolve failed for profile=%s", profile_id,
        )
    return None


def _build_job(spec: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    """Mirror hermes.cron.jobs.create_job's schema. Native agent mode:
    no script, the cron tick spawns the agent in-process with the
    given skill + prompt."""
    return {
        "id": uuid.uuid4().hex[:12],
        "name": spec["name"],
        "prompt": spec["prompt"],
        "skills": [spec["skill"]],
        "skill": spec["skill"],
        "model": None,
        "provider": None,
        "base_url": None,
        "script": None,
        "no_agent": False,
        "context_from": None,
        "schedule": {"kind": "cron", "expr": spec["schedule"], "display": spec["schedule"]},
        "schedule_display": spec["schedule"],
        "repeat": {"times": None, "completed": 0},
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": _now_iso(),
        "next_run_at": None,
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        "deliver": spec["deliver"],
        "deliver_render": spec.get("deliver_render"),
        "user_id": user_id,
        "origin": None,
        "enabled_toolsets": None,
        "workdir": None,
        "provisioned_by": "hermes_cloud.cron_provisioner",
    }


def ensure_default_jobs(
    profile_id: str, hermes_home: Path, *, user_id: int | None = None,
) -> dict:
    """Ensure every default job exists in this profile's jobs.json.

    Skips profiles with no active channels (no point running expensive
    agent work for nobody to receive). Removes any previously-
    provisioned jobs when the profile loses all bindings.

    For PROVISIONED jobs, reconcile drift against the spec:
    ``deliver``, ``schedule``, ``skill``, ``prompt``, ``script``,
    ``no_agent`` are forced back to spec values. User-created jobs
    (no ``provisioned_by`` marker) are never touched.
    """
    if not _profile_has_active_channel(hermes_home, profile_id):
        path = _jobs_file(hermes_home, profile_id)
        if path.exists():
            doc = _load_jobs(path)
            spec_names = {s["name"] for s in DEFAULT_JOBS}
            before = len(doc.get("jobs", []))
            doc["jobs"] = [
                j for j in doc.get("jobs", [])
                if not (
                    isinstance(j, dict)
                    and j.get("name") in spec_names
                    and j.get("provisioned_by") == "hermes_cloud.cron_provisioner"
                )
            ]
            removed = before - len(doc["jobs"])
            if removed:
                doc["updated_at"] = _now_iso()
                _atomic_write(path, doc)
                logger.info(
                    "cron_provisioner: profile=%s no active channels — removed %d provisioned jobs",
                    profile_id, removed,
                )
        return {"added": [], "kept": [], "updated": [], "skipped": "no_active_channels"}

    path = _jobs_file(hermes_home, profile_id)
    doc = _load_jobs(path)
    spec_by_name = {s["name"]: s for s in DEFAULT_JOBS}

    added: list[str] = []
    kept: list[str] = []
    updated: list[str] = []

    seen_names: set[str] = set()
    for job in doc.get("jobs", []):
        if not isinstance(job, dict):
            continue
        name = job.get("name")
        seen_names.add(name)
        spec = spec_by_name.get(name)
        if not spec or job.get("provisioned_by") != "hermes_cloud.cron_provisioner":
            kept.append(name)
            continue
        drift = []
        target = _build_job(spec, user_id)
        for k in (
            "deliver", "script", "no_agent", "skill", "skills", "prompt",
            "deliver_render",
        ):
            if job.get(k) != target.get(k):
                drift.append(f"{k}: {job.get(k)!r} -> {target.get(k)!r}")
                job[k] = target.get(k)
        if user_id is not None and job.get("user_id") != user_id:
            drift.append(f"user_id: {job.get('user_id')!r} -> {user_id!r}")
            job["user_id"] = user_id
        sched = job.get("schedule", {})
        if sched.get("expr") != spec["schedule"]:
            drift.append(f"schedule: {sched.get('expr')!r} -> {spec['schedule']!r}")
            job["schedule"] = target["schedule"]
            job["schedule_display"] = target["schedule_display"]
            job["next_run_at"] = None  # recompute on next tick
        if drift:
            updated.append(f"{name} ({'; '.join(drift)})")
        else:
            kept.append(name)

    for spec in DEFAULT_JOBS:
        if spec["name"] in seen_names:
            continue
        # A job whose delivery renders from this user's coach cards is
        # dead weight without a cloud user_id: every tick the agent
        # would burn LLM budget and the scheduler would fail to render.
        # Skip it (mirrors the no-active-channel guard's philosophy);
        # the next backfill provisions it once user_id resolves.
        if spec.get("requires_user_id") and user_id is None:
            logger.warning(
                "cron_provisioner: profile=%s — skipping job %r: it needs "
                "a cloud user_id to render its digest, which is unresolved",
                profile_id, spec["name"],
            )
            continue
        doc["jobs"].append(_build_job(spec, user_id))
        added.append(spec["name"])

    if added or updated:
        doc["updated_at"] = _now_iso()
        _atomic_write(path, doc)
        logger.info(
            "cron_provisioner: profile=%s added=%s updated=%s kept=%s",
            profile_id, added, updated, kept,
        )
    return {"added": added, "kept": kept, "updated": updated, "skipped": None}


def backfill_all_profiles(hermes_home: Path) -> dict:
    """Call ``ensure_default_jobs`` for every existing profile dir."""
    profiles_root = Path(hermes_home) / "profiles"
    if not profiles_root.exists():
        return {"profiles": 0, "added_total": 0, "updated_total": 0, "skipped_total": 0}
    profiles = 0
    added_total = 0
    updated_total = 0
    skipped_total = 0
    for entry in profiles_root.iterdir():
        if not entry.is_dir():
            continue
        profiles += 1
        res = ensure_default_jobs(
            entry.name, hermes_home,
            user_id=_resolve_profile_user_id(entry.name),
        )
        added_total += len(res.get("added") or [])
        updated_total += len(res.get("updated") or [])
        if res.get("skipped"):
            skipped_total += 1
    return {
        "profiles": profiles,
        "added_total": added_total,
        "updated_total": updated_total,
        "skipped_total": skipped_total,
    }


__all__ = [
    "DEFAULT_JOBS",
    "ensure_default_jobs",
    "backfill_all_profiles",
]
