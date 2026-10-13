"""Celery application — plan §三 async task queue.

Replaces the asyncio-based :mod:`scheduler` with a production-grade
Celery + Redis setup. Supports:

1. Periodic tasks via Celery beat (trust auto-adjust, RL training)
2. Async task execution (workflow processing, email sending)
3. Task result storage in Redis

Configuration:
- ``CELERY_BROKER_URL``: Redis URL for the broker (default: redis://localhost:6379/0)
- ``CELERY_RESULT_BACKEND``: Redis URL for results (default: same as broker)

Usage::

    # Start worker:
    celery -A hermes_cloud.app.celery_app worker -l info

    # Start beat (periodic tasks):
    celery -A hermes_cloud.app.celery_app beat -l info

    # Or combined:
    celery -A hermes_cloud.app.celery_app worker -B -l info
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_prerun, task_postrun

logger = logging.getLogger(__name__)

# v3.3.1 — emit one info log the first time the flag-gate skips Phase 5
# cloud-orchestrator paths in run_intake_for_interaction. Reset on worker boot.
_V331_CELERY_LOG_ONCE = False

broker_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", broker_url)

app = Celery(
    "hermes_cloud",
    broker=broker_url,
    backend=result_backend,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Don't lose tasks if the worker crashes mid-execution
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Task result expiration (24h)
    result_expires=86400,
)


def _register_set_safe_json() -> None:
    """Harden Celery's ``json`` serializer against ``set`` / ``frozenset``.

    Root cause of a silent class of failure: a CRM record write registers
    ``db.after_commit(...)`` callbacks that enqueue Celery tasks
    (``run_workflow_task`` / ``ai_fill_task``). If any task arg carried a
    ``set`` (e.g. a ``changed_fields`` diff built with ``set(...)``), kombu's
    stock json serializer raised ``EncodeError(TypeError('Object of type set
    is not JSON serializable'))``. That exception fires inside
    ``DbWrapper._drain_after_commit``, which catches and logs
    ``after_commit_callback_failed`` — so the dispatch (and any approval
    notification it would have produced) vanished silently.

    Fixing each call site with ``list(...)`` is whack-a-mole; the durable fix
    is at the serialization boundary. We subclass kombu's own ``JSONEncoder``
    (preserving its typed encoding for datetime / uuid / Decimal / bytes) and
    add a single ``set``/``frozenset`` → sorted-list branch, then re-register
    the ``json`` serializer under the same ``application/json`` content type
    so producers and workers stay wire-compatible (decode is unchanged —
    a set just arrives as a normal JSON array). ``sorted`` keeps the output
    deterministic so ``run_key`` dedup stays stable.
    """
    from kombu.serialization import register
    from kombu.utils.json import (
        JSONEncoder as _KombuJSONEncoder,
        dumps as _kombu_dumps,
        loads as _kombu_loads,
    )

    class _SetSafeJSONEncoder(_KombuJSONEncoder):
        def default(self, o: Any) -> Any:
            if isinstance(o, (set, frozenset)):
                return sorted(o, key=str)
            return super().default(o)

    def _dumps(obj: Any) -> str:
        return _kombu_dumps(obj, cls=_SetSafeJSONEncoder)

    register(
        "json",
        _dumps,
        _kombu_loads,
        content_type="application/json",
        content_encoding="utf-8",
    )


_register_set_safe_json()

# ---------------------------------------------------------------------------
# Heartbeats — every beat task writes one row to cron_heartbeats on
# prerun/postrun. /insights/cron_health reads this table so the SPA can
# show liveness for tasks that don't emit gateway_actions (retention
# sweeps, weekly digest, memory ingest…) — previously these were
# silently invisible.
# ---------------------------------------------------------------------------

_BEAT_TASK_TO_HEARTBEAT_NAME = {
    "hermes_cloud.app.celery_app.memory_ingest_recent": "memory-ingest-recent",
    "hermes_cloud.app.celery_app.agent_messages_retention": "agent-messages-retention",
    "hermes_cloud.app.celery_app.audit_log_retention": "audit-log-retention",
}

_HEARTBEAT_SCHEMA_READY = False
_HEARTBEAT_TASK_STARTS: dict[str, float] = {}


def _ensure_heartbeat_schema():
    """Create cron_heartbeats if it doesn't exist. Idempotent — called
    once per worker process on first heartbeat write."""
    try:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS cron_heartbeats (
                    name             TEXT PRIMARY KEY,
                    last_run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_status      TEXT NOT NULL,
                    last_duration_ms INTEGER,
                    last_detail      JSONB,
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """,
                (),
            )
            db.commit()
    except Exception:
        logger.exception("cron_heartbeats: schema ensure failed")


def _write_heartbeat(name: str, status: str,
                     duration_ms: int | None = None,
                     detail: dict | None = None) -> None:
    """UPSERT one heartbeat row. status ∈ {'started','ok','error'}."""
    global _HEARTBEAT_SCHEMA_READY
    if not _HEARTBEAT_SCHEMA_READY:
        _ensure_heartbeat_schema()
        _HEARTBEAT_SCHEMA_READY = True
    try:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.execute(
                """
                INSERT INTO cron_heartbeats
                    (name, last_run_at, last_status,
                     last_duration_ms, last_detail, updated_at)
                VALUES (%s, now(), %s, %s, %s::jsonb, now())
                ON CONFLICT (name) DO UPDATE SET
                    last_run_at      = EXCLUDED.last_run_at,
                    last_status      = EXCLUDED.last_status,
                    last_duration_ms = EXCLUDED.last_duration_ms,
                    last_detail      = EXCLUDED.last_detail,
                    updated_at       = now()
                """,
                (
                    name, status, duration_ms,
                    json.dumps(detail) if detail is not None else None,
                ),
            )
            db.commit()
    except Exception:
        logger.exception("cron_heartbeats: write failed name=%s", name)


@task_prerun.connect
def _heartbeat_prerun(sender=None, task_id=None, task=None, **_kwargs):
    name = _BEAT_TASK_TO_HEARTBEAT_NAME.get(getattr(task, "name", "") or "")
    if not name or not task_id:
        return
    _HEARTBEAT_TASK_STARTS[task_id] = time.time()
    _write_heartbeat(name, "started")


@task_postrun.connect
def _heartbeat_postrun(sender=None, task_id=None, task=None,
                       state=None, retval=None, **_kwargs):
    name = _BEAT_TASK_TO_HEARTBEAT_NAME.get(getattr(task, "name", "") or "")
    if not name or not task_id:
        return
    start_ts = _HEARTBEAT_TASK_STARTS.pop(task_id, None)
    duration_ms = (
        int((time.time() - start_ts) * 1000) if start_ts is not None else None
    )
    status = "ok" if str(state) == "SUCCESS" else "error"
    detail: dict[str, Any] | None = None
    # Capture small dict returns (totals, counts) for SPA visibility.
    if isinstance(retval, dict) and len(retval) <= 12:
        try:
            json.dumps(retval)  # ensure serializable
            detail = retval
        except (TypeError, ValueError):
            detail = None
    _write_heartbeat(name, status, duration_ms=duration_ms, detail=detail)


# ---------------------------------------------------------------------------
# Periodic tasks (Celery beat schedule)
# ---------------------------------------------------------------------------

app.conf.beat_schedule = {
    "memory-ingest-recent": {
        "task": "hermes_cloud.app.celery_app.memory_ingest_recent",
        "schedule": 300.0,  # every 5 minutes
    },
    # Charter v2 §定律 4 — old rule-based coach paths retired in favor
    # of crm-daily-coach skill (see crm-daily-coach beat below).
    # Doc: docs/2026-05-15-coach-remediation-charter-v2.md
    # crm-daily-coach + weekly-digest moved to per-profile hermes cron
    # (~/.hermes/profiles/<id>/cron/jobs.json) under charter v2 "agent
    # is core, cloud is auxiliary" — see Phase C/D 2026-05-18 migration.
    # Charter §一原语 ① + §六定律 6 — agent_messages retention.
    # Mirrors hermes upstream's auto-prune-on-startup feature
    # (b8663813); on PG we run as a daily beat instead of a per-process
    # startup hook because cloud has many short-lived worker processes.
    # Default: keep 90 days. Tunable via env HERMES_AGENT_MESSAGES_RETENTION_DAYS.
    "agent-messages-retention-daily": {
        "task": "hermes_cloud.app.celery_app.agent_messages_retention",
        "schedule": crontab(hour=4, minute=0),  # daily at 04:00 UTC
    },
    # Phase 5.5 — audit_log retention sweep. Monthly cron archives
    # rows older than 90 days to S3-like storage (or local fs by
    # default) then DELETEs them from PG. First-of-month at 04:30 UTC
    # to avoid clashing with the agent_messages retention above.
    "audit-log-retention-monthly": {
        "task": "hermes_cloud.app.celery_app.audit_log_retention",
        "schedule": crontab(hour=4, minute=30, day_of_month=1),
    },
    # 工作流 schedule 触发:每分钟扫一次哪些 cron 到点,入 run_workflow 队列。
    "workflow-scheduler-tick": {
        "task": "hermes_cloud.app.celery_app.workflow_scheduler_tick",
        "schedule": 60.0,  # every minute
    },
}


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------


# Charter v2 §定律 4 cleanup — `coach_hints_scan` and
# `coach_orphan_commitment_scan` retired. The crm-daily-coach skill is
# now driven by hermes cron per profile (see hermes_cloud.app.cron_provisioner),
# not by celery. See docs/2026-05-15-coach-remediation-charter-v2.md.


@app.task(name="hermes_cloud.app.celery_app.ai_fill_task")
def ai_fill_task(module: str, record_id: int, fields=None, trigger: str = "on_change"):
    """AI 字段填值(事件触发入队)。详见 crm/ai_attributes.fill_record。"""
    try:
        from hermes_cloud.app.crm.ai_attributes import fill_record
        return fill_record(module_name=str(module), record_id=int(record_id),
                           fields=fields, trigger=str(trigger))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_fill_task_failed mod=%s rec=%s err=%s", module, record_id, exc)
        return {"status": "failed", "error": str(exc)}


@app.task(name="hermes_cloud.app.celery_app.ai_fill_backfill_task")
def ai_fill_backfill_task(module: str, field_name: str, batch: int = 50, sleep_sec: float = 1.0):
    """新建 AI 字段后异步分批回填存量记录。"""
    try:
        import time
        from hermes.crm.db import db_session
        from hermes_cloud.app.crm.ai_attributes import fill_record
        pk = f"{module}_id"
        last = 0
        while True:
            with db_session() as db:
                rows = db.fetchall(
                    f'SELECT {pk} AS id FROM "{module}" WHERE {pk} > %s '
                    f'AND (deleted IS NULL OR deleted = FALSE) ORDER BY {pk} ASC LIMIT %s',
                    (last, int(batch))) or []
            if not rows:
                break
            for r in rows:
                last = int(r["id"])
                try:
                    fill_record(module_name=module, record_id=last,
                                fields=[field_name], trigger="backfill")
                except Exception:
                    logger.debug("backfill_row_skipped mod=%s rec=%s", module, last, exc_info=True)
            time.sleep(float(sleep_sec))
        return {"status": "done", "last": last}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_fill_backfill_failed mod=%s field=%s err=%s", module, field_name, exc)
        return {"status": "failed", "error": str(exc)}


@app.task(name="hermes_cloud.app.celery_app.run_workflow_task")
def run_workflow_task(workflow_id: int, record_id=None, trigger: str = "event",
                      cascade_depth: int = 0, changed_fields=None, run_key=None):
    """执行一条工作流(event 命中或 schedule 到点入队)。引擎动作直接 Save、
    agent 动作经 /internal/utility/run-skill。详见 crm/workflows.run_workflow。

    ``cascade_depth``:级联深度(默认 0,向后兼容)。工作流自身写引发的下游派发会
    传 +1,超 MAX_CASCADE 时 run_workflow 直接 skip(防链式自激)。
    ``changed_fields``:本次写变更的字段名(event 触发由 emit_business_write 串入),
    供 ``changed`` 条件/守卫判断"字段变化时"。"""
    try:
        from hermes_cloud.app.crm.workflows import run_workflow
        return run_workflow(int(workflow_id),
                            None if record_id is None else int(record_id),
                            str(trigger),
                            cascade_depth=int(cascade_depth or 0),
                            changed_fields=changed_fields,
                            run_key=run_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_workflow_task_failed wf=%s rec=%s err=%s",
                       workflow_id, record_id, exc)
        return {"status": "failed", "error": str(exc)}


@app.task(name="hermes_cloud.app.celery_app.workflow_scheduler_tick")
def workflow_scheduler_tick():
    """每分钟跑:找到 cron 到点的 schedule 工作流,扫描其模块里命中条件的记录,
    逐条入 run_workflow_task。无到点工作流则零成本返回。"""
    from datetime import datetime, timezone
    from hermes.crm.db import db_session
    from hermes_cloud.app.crm.workflows import (
        due_schedule_workflows, evaluate_condition, _record_pk,
    )
    now = datetime.now(timezone.utc)
    enqueued = 0
    with db_session() as db:
        due = due_schedule_workflows(db, now)
        for wid in due:
            wf = db.fetchone("SELECT * FROM workflows WHERE id = %s", (wid,))
            if not wf:
                continue
            module = wf["module"]
            pk = _record_pk(module)
            # 扫描模块全部未删记录,逐条复核条件(条件在 run_workflow 也会再核)
            try:
                rows = db.fetchall(
                    f'SELECT * FROM "{module}" '
                    f"WHERE COALESCE(deleted::text,'0') NOT IN ('1','t','true')") or []
            except Exception:
                rows = []
            # 本次 tick 的实例键(分钟桶):同一 tick 重复投递挡住,下个 tick 放行。
            tick = now.strftime("%Y%m%dT%H%M")
            for r in rows:
                if evaluate_condition(dict(r), wf.get("condition")):
                    run_workflow_task.apply_async(
                        args=[wid, int(r[pk]), "schedule"],
                        kwargs={"run_key": f"sched:{tick}:{int(r[pk])}"})
                    enqueued += 1
            # 即便没有命中记录,也推进 last_run_at,避免下一 tick 重复判定到点
            db.execute("UPDATE workflows SET last_run_at = %s WHERE id = %s", (now, wid))
        db.commit()
    return {"due": len(due), "enqueued": enqueued}


@app.task(name="hermes_cloud.app.celery_app.agent_messages_retention")
def agent_messages_retention(retention_days: int | None = None) -> dict:
    """Prune ``agent_sessions`` (+ their messages via CASCADE) ended
    longer than the retention window ago. Charter §一原语 ① keeps
    the upstream Interaction Log append-only by design; this task only
    bounds the ``agent_sessions`` / ``agent_messages`` session backing.

    Semantics: a session is pruned when ``ended_at IS NOT NULL`` and
    ``ended_at < now - retention_days``. **Active** sessions (ended_at
    NULL) are never touched so the agent loop's compaction can still
    read its full history. ``agent_messages`` rows go via the FK
    CASCADE; PG's autovacuum reclaims storage.

    Default 90 days. Tunable via env ``HERMES_AGENT_MESSAGES_RETENTION_DAYS``.
    Set to 0 or negative to disable (compliance archives).

    Returns ``{"deleted_sessions": int, "deleted_messages": int,
    "skipped": bool}``.
    """
    import os
    import time
    if retention_days is None:
        try:
            retention_days = int(os.environ.get(
                "HERMES_AGENT_MESSAGES_RETENTION_DAYS", "90",
            ))
        except (TypeError, ValueError):
            retention_days = 90
    if retention_days <= 0:
        return {"deleted_sessions": 0, "deleted_messages": 0, "skipped": True}

    try:
        from hermes.crm.db import db_session
        with db_session() as db:
            cutoff = time.time() - (int(retention_days) * 86400)
            # Count messages about to drop (for telemetry; the DELETE
            # itself goes via FK CASCADE so we can't RETURNING on the
            # session DELETE alone).
            n_msgs = int(db.fetchcolumn(
                """
                SELECT count(*) FROM agent_messages m
                  JOIN agent_sessions s ON s.id = m.session_id
                 WHERE s.ended_at IS NOT NULL AND s.ended_at < %s
                """,
                (cutoff,),
            ) or 0)
            rows = db.fetchall(
                "DELETE FROM agent_sessions "
                " WHERE ended_at IS NOT NULL AND ended_at < %s "
                "RETURNING id",
                (cutoff,),
            ) or []
            n_sessions = len(rows)
        if n_sessions:
            logger.info(
                "agent_messages_retention pruned sessions=%d messages=%d days=%d",
                n_sessions, n_msgs, retention_days,
            )
        return {
            "deleted_sessions": n_sessions,
            "deleted_messages": n_msgs,
            "skipped": False,
        }
    except Exception:
        logger.exception("agent_messages_retention failed")
        raise


@app.task(name="hermes_cloud.app.celery_app.memory_ingest_recent")
def memory_ingest_recent(since_minutes: int = 10, limit: int = 500):
    """Periodic Memory Substrate ingestion — charter 原语 ②.

    Pulls interactions written in the last ``since_minutes`` minutes
    that don't yet have an associated ``memories`` row and distills
    them. Safe to run on a tight schedule (default every 5 minutes):
    idempotent via a ``source_refs`` uniqueness check.
    """
    try:
        from hermes.memory.ingest import ingest_batch
        n = ingest_batch(since_minutes=since_minutes, limit=limit)
        if n:
            logger.info("memory_ingest_recent ingested=%d", n)
        return {"ingested": n}
    except Exception:
        logger.exception("memory_ingest_recent failed")
        raise


def _interaction_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    parts: list[str] = []
    for key in ("summary", "text", "body", "message", "subject"):
        v = content.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return " ".join(parts).strip()


def _first_user_id(actors: Any) -> int | None:
    if not isinstance(actors, list):
        return None
    for actor in actors:
        if (
            isinstance(actor, dict)
            and str(actor.get("type") or "") == "user"
            and actor.get("id") is not None
        ):
            try:
                return int(actor["id"])
            except (TypeError, ValueError):
                continue
    return None


def _first_profile_id(actors: Any) -> str:
    if not isinstance(actors, list):
        return ""
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        profile_id = actor.get("profile_id")
        if profile_id not in (None, ""):
            return str(profile_id)
    return ""


@app.task(name="hermes_cloud.app.celery_app.run_intake_for_interaction")
def run_intake_for_interaction(interaction_id: str, profile_id: str = ""):
    """Handle one inbound interaction.

    Charter v2 Phase 2 (2026-05-05): the cloud-orchestrator specialist
    paths (intake / coach / email_drafter / compliance) were folded
    into markdown skills the main agent reads directly. The only work
    that still belongs here is the gateway text-command short-circuit
    (``通过 <id>`` / ``拒绝 <id>`` / ``撤销 <id>`` / ``改 <id>`` on
    non-webui channels). All other inbound text is now handled by the
    main agent inside the per-profile hermes subprocess.
    """
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            row = db.fetchone(
                """
                SELECT id, kind, actors, subjects, content
                  FROM interactions
                 WHERE id = %s
                """,
                (interaction_id,),
            )
            if not row:
                logger.warning(
                    "run_intake_for_interaction_missing interaction_id=%s",
                    interaction_id,
                )
                return {"ok": False, "reason": "interaction_not_found"}

            user_id = _first_user_id(row.get("actors"))
            if not user_id:
                logger.warning(
                    "run_intake_for_interaction_no_user interaction_id=%s",
                    interaction_id,
                )
                return {"ok": False, "reason": "missing_user"}

            inferred_profile = _first_profile_id(row.get("actors"))
            effective_profile = str(profile_id or inferred_profile or "")
            raw_text = _interaction_text(row.get("content"))

            # Charter §1 渠道闭环: bind the originating channel so any
            # gateway-routed reply (approval/reject/revert ack) goes
            # back to the same room.
            from hermes_cloud.app.bridge import origin_context

            origin = origin_context.from_interaction_actors(
                actors=row.get("actors"),
                interaction_id=str(row.get("id")),
            )

            # ── Priority 1: revert / approval / edit text command ──────
            # If the inbound text is "通过 <prefix>" / "拒绝 <prefix>" /
            # "撤销 <prefix>" / "改 <prefix>" on a non-webui channel,
            # route it into dispatcher.approve/reject/revert and
            # short-circuit the agent (no field extraction / CRM write —
            # the user is answering an outstanding approval or undoing
            # a previous write, not reporting new info).
            channel = str(origin.get("channel") or "webui")
            if channel != "webui":
                from hermes.action_gateway import text_commands
                reply = text_commands.try_approval_command(
                    raw_text,
                    user_id=int(user_id),
                    via_channel=channel,
                    db=db,
                )
                if reply is not None:
                    from hermes.action_gateway import channel_reply
                    channel_reply.reply_via_origin(
                        origin=origin,
                        profile_id=effective_profile or str(user_id),
                        text=reply,
                    )
                    logger.info(
                        "run_intake_for_interaction_gateway_command "
                        "interaction_id=%s channel=%s reply=%r",
                        interaction_id, channel, reply,
                    )
                    return {"ok": True, "gateway_command": True, "reply": reply}

            # Charter v2 Phase 2 (2026-05-05): the cloud-orchestrator
            # specialist paths (intake / coach / email_drafter /
            # compliance) were removed when those specialists were
            # folded into markdown skills. All inbound chat input now
            # defers to the main agent inside the per-profile hermes
            # subprocess; this celery task's only remaining job is the
            # gateway text-command short-circuit handled above.
            global _V331_CELERY_LOG_ONCE
            if not _V331_CELERY_LOG_ONCE:
                logger.info(
                    "specialists removed, deferring to hermes main agent "
                    "(path=celery.run_intake_for_interaction)"
                )
                _V331_CELERY_LOG_ONCE = True
            return {
                "ok": True,
                "skipped": "specialists_folded_into_skills",
                "interaction_id": str(interaction_id),
            }
    except Exception:
        logger.exception(
            "run_intake_for_interaction_failed interaction_id=%s",
            interaction_id,
        )
        raise


# Charter v2 Phase 3 (2026-05-06): ``run_rl_training`` celery task
# removed along with the RL subsystem (rl/ + feedback/). Off-line
# training is no longer scheduled or accepted via celery.


@app.task(name="hermes_cloud.app.celery_app.audit_log_retention")
def audit_log_retention(
    retention_days: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Phase 5.5 — monthly archive + delete of old audit_log rows.

    Wraps :func:`hermes_cloud.app.scope.retention.cleanup_audit_log`.
    Default 90 days; tunable via env ``HERMES_AUDIT_LOG_RETENTION_DAYS``.
    First-of-month at 04:30 UTC (see beat_schedule above).

    The default writer dumps to the local fs at
    ``data/audit_archive/YYYY-MM.ndjson``. Production deployments
    should override via a wrapper task that injects an S3-backed
    writer; this default is fine for single-node setups.

    Returns ``{"archived": int, "deleted": int, "skipped": bool}``.
    """
    import os
    if retention_days is None:
        try:
            retention_days = int(os.environ.get(
                "HERMES_AUDIT_LOG_RETENTION_DAYS", "90",
            ))
        except (TypeError, ValueError):
            retention_days = 90
    if retention_days <= 0:
        return {"archived": 0, "deleted": 0, "skipped": True}

    try:
        from hermes.crm.db import db_session
        from hermes_cloud.app.scope.retention import cleanup_audit_log
        with db_session() as db:
            result = cleanup_audit_log(
                db, retention_days=retention_days, dry_run=dry_run,
            )
        result["skipped"] = False
        logger.info(
            "audit_log_retention archived=%s deleted=%s%s",
            result.get("archived", 0),
            result.get("deleted", 0),
            f" error={result['error']!r}" if "error" in result else "",
        )
        return result
    except Exception:
        logger.exception("audit_log_retention failed")
        return {"archived": 0, "deleted": 0, "error": "exception"}
