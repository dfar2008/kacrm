"""``/api/insights/weekly`` — "本周 AI 表现" feed (ChatInbox empty state).

Returns a single JSON payload the frontend turns into a 2–3 sentence
narrative plus up to 3 Coach hints. Scoped to the current user (records
where ``owner_by = user_id``). **Deliberately omits draft adoption
metrics** — those are internal training signals per 宪章 定律 3, not
user-facing KPIs.

Payload shape::

    {
      "has_data": bool,
      "period_days": 7,
      "outcomes": {
        "deals_won":        <int>,
        "deals_won_amount": <float | null>,
        "stages_advanced":  <int>,
        "first_touch":      <int>
      },
      "coach_hints": [
        { "severity": "high|medium|low",
          "signal":   "pipeline_silence|vague_touchpoint|missing_next_action|repeated_field_correction",
          "module":   "potentials|leads",
          "record_id": 12, "record_name": "...",
          "hint": "14 天未联系，商机在方案阶段" },
        ...
      ]
    }

If neither outcomes nor hints produce anything the endpoint returns
``has_data: false`` and the frontend hides the whole card.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from hermes_cloud.app.auth import require_dashboard_auth
from hermes_cloud.app.config import AppConfig
from hermes.crm.db import db_session
from hermes.crm.insights.feed import (
    _build_daily_feed,
    _read_hints_from_table,
    _recent_interactions_for_record,
    _resolve_record_name,
    _scan_outcomes,
)
from hermes.crm.insights.priority import (
    _build_today_priority,
    _fallback_suggestions,
    _fallback_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _get_config(request: Request) -> AppConfig:
    return getattr(request.app.state, "config", None) or AppConfig()


def _current_user_id(request: Request) -> int:
    """Dashboard auth already resolved the session; re-extract here so
    we can scope per-user. Falls back to user_id=1 in open-dev mode."""
    uid, _ = require_dashboard_auth(request, _get_config(request))
    try:
        return int(uid) if uid else 1
    except (TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Outcome aggregation, hint reads, record-name resolution, and the daily
# feed builder moved to ``hermes.crm.insights.feed``. Coach scan moved to
# ``insights.scanners.coach.scan_user``.
# ---------------------------------------------------------------------------


@router.get("/weekly")
def weekly_insights(request: Request):
    """Per-user weekly insight feed for the ChatInbox-empty-state card.

    Outcomes are still computed live (cheap aggregation). Coach hints
    are now read from ``coach_hints`` — the Celery job in
    :func:`app.celery_app.coach_hints_scan` populates the table every
    30 minutes.
    """
    user_id = _current_user_id(request)
    days = 7
    try:
        with db_session() as db:
            outcomes = _scan_outcomes(db, user_id, days)
            hints = _read_hints_from_table(db, user_id, limit=3)
    except Exception:
        logger.exception("weekly_insights_scan_failed user=%s", user_id)
        raise HTTPException(500, "insights_scan_failed")

    any_outcome = any(
        (v or 0) > 0 for v in (
            outcomes["deals_won"],
            outcomes["stages_advanced"],
            outcomes["first_touch"],
        )
    )
    return {
        "has_data": bool(any_outcome or hints),
        "period_days": days,
        "outcomes": outcomes,
        "coach_hints": hints,
    }


# ---------------------------------------------------------------------------
# Coach-hint inbox operations — STUBBED in charter v2 §定律 4 cleanup.
# The v1 rule-based ``coach_hints_scan`` was replaced by main agent +
# ``crm-daily-coach`` skill emitting ``coach.proposal`` cards into
# ``gateway_actions``. These endpoints return empty / no-op so older
# SPA code (RecordDetail unread-badge) still loads without 500.
# See docs/2026-05-15-coach-remediation-charter-v2.md.
# ---------------------------------------------------------------------------
@router.post("/coach-hints/{hint_id}/read")
def mark_hint_read(hint_id: int, request: Request):
    _current_user_id(request)
    return {"ok": True, "id": int(hint_id), "deprecated": True}


@router.post("/coach-hints/{hint_id}/dismiss")
def dismiss_hint(hint_id: int, request: Request):
    _current_user_id(request)
    return {"ok": True, "id": int(hint_id), "deprecated": True}


@router.get("/coach-hints/by-record")
def list_hints_for_record(
    request: Request, module: str, record_id: int,
    include_read: bool = False,
):
    _current_user_id(request)
    return {"hints": []}


@router.get("/coach-hints/unread-count")
def unread_hint_count(
    request: Request,
    module: Optional[str] = None,
    record_id: Optional[int] = None,
):
    _current_user_id(request)
    return {"unread": 0}


@router.post("/coach-hints/scan-now")
def scan_now(request: Request):
    _current_user_id(request)
    return {"ok": True, "stats": {}, "scanned": 0, "deprecated": True}


# ---------------------------------------------------------------------------
# In-flight LLM generations (single-flight), keyed by a namespaced tuple:
#   ("record", module, record_id, signature)     — /api/insights/record
#   ("list_brief", user_id, module, signature)   — /api/insights/list_brief
# A generation takes seconds to tens of seconds of agent time before the
# cache is written, so without this every concurrent cache-miss (dev
# StrictMode double-fires, two components fetching the same view, rapid
# back-and-forth navigation to the same record) spawned its own full LLM
# run for an identical result. Per-process only — acceptable because the
# backend runs single-process and a cross-worker duplicate is idempotent
# (same content, upsert).
# ---------------------------------------------------------------------------
_INFLIGHT_GENERATIONS: Dict[tuple, "asyncio.Task"] = {}


def _spawn_or_join_generation(key: tuple, factory) -> "asyncio.Task":
    """Single-flight: return the live generation task for ``key``,
    creating it via ``factory`` only when none is in flight. Finished
    tasks (success or failure) are evicted by their done-callback, so a
    failed generation never poisons the key."""
    task = _INFLIGHT_GENERATIONS.get(key)
    if task is not None and not task.done():
        return task
    task = asyncio.create_task(factory())

    def _evict(t, k=key):
        if _INFLIGHT_GENERATIONS.get(k) is t:
            _INFLIGHT_GENERATIONS.pop(k, None)

    _INFLIGHT_GENERATIONS[key] = task
    task.add_done_callback(_evict)
    return task


# ---------------------------------------------------------------------------
# /api/insights/record — per-record AI briefing (CopilotPanel empty state)
# ---------------------------------------------------------------------------
async def _generate_and_cache(
    *, module: str, record_id: int, signature: str,
    record_name: Optional[str], summary: Optional[str],
    hints: List[Dict[str, Any]], recent_interactions: List[Dict[str, Any]],
    user_id: Optional[int] = None,
    app: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Background task: run the LLM generator + write the cache, with
    its own db_session so it survives the originating request's
    lifecycle. Returns the questions (caller may use them if it's
    waiting; if not, the cache write is the only side effect that
    matters)."""
    from hermes.crm.insights.question_cache import set_cached_questions
    from hermes.crm.insights.question_generator import generate_questions

    try:
        questions = await generate_questions(
            module=module, record_name=record_name,
            user_id=user_id,
            summary=summary, hints=hints,
            recent_interactions=recent_interactions,
            app=app,
        )
    except Exception:
        logger.exception(
            "record_insight_bg_llm_crashed module=%s id=%s",
            module, record_id,
        )
        return []

    if questions:
        try:
            with db_session() as bg_db:
                set_cached_questions(
                    bg_db, module=module, record_id=record_id,
                    signature=signature, questions=questions,
                )
        except Exception:
            logger.exception(
                "record_insight_bg_cache_write_failed module=%s id=%s",
                module, record_id,
            )
    return questions


@router.get("/record")
async def record_insight(request: Request, module: str, id: int):
    """Per-record briefing: 1–2 sentence summary + up to 3 Coach hints +
    up to 3 suggested verb-led questions.

    Two-stage question pipeline:
      1. Per-module scanner (``insights/scanners/``) computes summary,
         hints, and signal-driven template questions (Phase A).
      2. If we have an LLM-generated cache hit for the current
         (record, signature), swap in those richer questions. Else
         spawn an LLM call to generate fresh ones; on success cache
         and return; on failure keep the template questions.

    The cache lives in ``record_question_cache`` keyed by
    (module, record_id), invalidated whenever the record's hint
    signals, latest interaction, or display name change (see
    question_cache.compute_signature). TTL also bounds staleness.
    """
    from hermes.crm.insights.registry import get as _get_scanner
    from hermes.crm.insights.question_cache import (
        compute_signature, get_cached_questions, set_cached_questions,
        latest_interaction_id,
    )
    from hermes.crm.insights.question_generator import generate_questions

    user_id = _current_user_id(request)
    scanner = _get_scanner(module)
    if scanner is None:
        return {
            "has_data": False,
            "module": module,
            "record_id": id,
            "summary": None,
            "hints": [],
            "suggested_questions": [],
        }
    _src_holder: Dict[str, str] = {"v": "no_data"}
    return await _record_insight_inner(
        request=request, module=module, id=id,
        scanner=scanner, user_id=user_id,
        src_holder=_src_holder,
    )


async def _record_insight_inner(
    *, request: Request, module: str, id: int, scanner, user_id: int,
    src_holder: Dict[str, str],
) -> Dict[str, Any]:
    from hermes.crm.insights.question_cache import (
        compute_signature, get_cached_questions,
        latest_interaction_id,
    )
    try:
        with db_session() as db:
            payload = scanner(db, int(id), user_id)
            if not payload.get("has_data"):
                src_holder["v"] = "no_data"
                return payload

            hints = payload.get("hints") or []
            signature = compute_signature(
                record_name=payload.get("record_name") or _resolve_record_name(
                    db, module, int(id),
                ),
                hint_signals=[h.get("signal") for h in hints if h.get("signal")],
                last_interaction_id=latest_interaction_id(
                    db, module=module, record_id=int(id),
                ),
            )
            cached = get_cached_questions(
                db, module=module, record_id=int(id), signature=signature,
            )
            if cached:
                payload["suggested_questions"] = cached
                payload["question_source"] = "llm_cached"
                src_holder["v"] = "llm_cached"
                return payload

            # Cache miss — try the LLM. Pull recent interactions in the
            # SAME db session so everything's consistent.
            recent = _recent_interactions_for_record(
                db, module, int(id), limit=5,
            )
            record_name = payload.get("record_name") or _resolve_record_name(
                db, module, int(id),
            )

        # Drop the db session before the LLM call. Two-phase strategy:
        #
        #   1. Spawn the LLM generation as a detached asyncio task that
        #      writes the cache on completion (single-flight per key, so
        #      concurrent misses for the same record join one task). The
        #      task uses a fresh db_session, so it survives even if the
        #      request is cancelled (e.g. the user navigates away).
        #
        #   2. Race the task against a short deadline (3s). If it
        #      completes in time, return its result with
        #      source=llm_fresh. If not, return the scanner template
        #      with source=llm_pending — the bg task keeps running and
        #      writes the cache when done, so the user's NEXT visit to
        #      this record gets the fresh LLM questions instantly.
        gen_task = _spawn_or_join_generation(
            ("record", module, int(id), signature),
            lambda: _generate_and_cache(
                module=module, record_id=int(id),
                signature=signature, record_name=record_name,
                summary=payload.get("summary"),
                hints=hints, recent_interactions=recent,
                user_id=user_id,
                app=request.app,
            ),
        )
        try:
            llm_questions = await asyncio.wait_for(
                asyncio.shield(gen_task), timeout=3.0,
            )
        except asyncio.TimeoutError:
            llm_questions = []  # bg task still running

        if llm_questions:
            payload["suggested_questions"] = llm_questions
            payload["question_source"] = "llm_fresh"
            src_holder["v"] = "llm_fresh"
        else:
            # Either the LLM is still running (gen_task continues in
            # bg, will populate cache), or it already failed and the
            # bg task wrote nothing. Either way, the scanner-template
            # fallback the scanner already wrote into payload remains.
            src = "llm_pending" if not gen_task.done() else "scanner_template"
            payload["question_source"] = src
            src_holder["v"] = src
        return payload
    except Exception:
        logger.exception("record_insight_scan_failed module=%s id=%s", module, id)
        src_holder["v"] = "error"
        raise HTTPException(500, "insights_scan_failed")


# ---------------------------------------------------------------------------
# /api/insights/daily_feed — mixed feed of interactions + coach_hints +
# money snapshots for the /crm default-page event stream and per-module
# "今日动态" cards. The ``_build_daily_feed`` builder and its helpers
# (``_jsonb`` / ``_resolve_record_name``) moved to
# ``hermes.crm.insights.feed``.
# ---------------------------------------------------------------------------


@router.get("/daily_feed")
def daily_feed_endpoint(
    request: Request,
    hours: int = 24,
    limit: int = 200,
    module: Optional[str] = None,
):
    user_id = _current_user_id(request)
    hours = max(1, min(int(hours or 24), 24 * 14))
    limit = max(1, min(int(limit or 200), 500))
    try:
        with db_session() as db:
            return _build_daily_feed(
                db, user_id, hours=hours, limit=limit, module=module,
            )
    except Exception:
        logger.exception("daily_feed_failed user=%s", user_id)
        raise HTTPException(500, "daily_feed_failed")


# ---------------------------------------------------------------------------
# /api/insights/today_priority — cross-module urgency ranking that powers
# DailyFeed's top "🚨 今天最该处理" section.
#
# Charter v2 alignment:
#   §定律 1 (events-first): scores derive from events (interactions
#       silence + coach_hints) and event-driven projections
#       (next_contact_date), never from "this record looks important"
#       record-level hand-waving.
#   §定律 5 (AI 驾驶, 人类监督): the ranking is pure SQL — deterministic,
#       cheap, no LLM. Surfaces automatically at the top of the feed
#       without the user asking.
#
# The ``_build_today_priority`` ranking and the ``_suggest_next_action``
# signal→action mapping moved to ``hermes.crm.insights.priority``.
# ---------------------------------------------------------------------------


async def _generate_brief_and_cache(
    *, user_id: int, module: Optional[str], signature: str,
    summary_facts: Dict[str, Any],
    ai_noticed: List[Dict[str, Any]],
    top_priority: List[Dict[str, Any]],
    today_outcomes: Dict[str, Any],
    app: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Background task: LLM-generate the brief, cache it, return the
    result. Uses its own db_session — survives the originating
    request's lifecycle (Plan C bg-task pattern)."""
    from hermes.crm.insights.brief_cache import set_cached_brief
    from hermes.crm.insights.brief_generator import generate_brief

    try:
        brief = await generate_brief(
            summary_facts=summary_facts,
            ai_noticed=ai_noticed,
            top_priority=top_priority,
            today_outcomes=today_outcomes,
            user_id=user_id,
            app=app,
        )
    except Exception:
        logger.exception(
            "brief_bg_llm_crashed user=%s module=%s", user_id, module,
        )
        return None
    if brief:
        try:
            with db_session() as bg_db:
                set_cached_brief(
                    bg_db, user_id=user_id, module=module,
                    signature=signature, brief=brief,
                )
        except Exception:
            logger.exception(
                "brief_bg_cache_write_failed user=%s module=%s",
                user_id, module,
            )
    return brief


@router.get("/list_brief")
async def list_brief_endpoint(
    request: Request, module: Optional[str] = None,
):
    """Charter §定律 5: list-mode Copilot must be as proactive as
    record-mode. Returns the brief the panel renders in its empty
    state on /crm and per-module dashboards.

    Shape::

        {
          "module":     "accounts" | null,
          "summary":    "...",  # one-line LLM-generated read on the day
          "ai_noticed": [        # cross-record patterns (deterministic)
              {"signal","severity","text","payload"}, ...
          ],
          "suggestions": [       # 3 verb-led prompts (LLM-generated)
              {"text","agent"}, ...
          ],
          "counts":     {...},   # interactions / agent_recent / hints / outcomes
          "source":     "llm_cached" | "llm_fresh" | "llm_pending"
                      | "scanner_template",
        }

    Bg-task pattern (mirrors record_insight): if the LLM generation
    misses cache and takes >3s, we return the deterministic parts +
    a template summary; the bg task finishes and writes cache so the
    user's next visit is instant.
    """
    user_id = _current_user_id(request)
    src_holder: Dict[str, str] = {"v": "error"}
    return await _list_brief_inner(user_id, module, src_holder, app=request.app)


async def _list_brief_inner(
    user_id: int, module: Optional[str], src_holder: Dict[str, str],
    *, app: Optional[Any] = None,
) -> Dict[str, Any]:
    from hermes.crm.insights.ai_noticed import compute_ai_noticed
    from hermes.crm.insights.brief_cache import (
        compute_signature, get_cached_brief,
    )

    from datetime import date as _date

    today_local = _date.today()

    # Pull aggregate counts for the day window — same source as the
    # daily_feed's headline numbers so the brief stays consistent.
    try:
        with db_session() as db:
            feed = _build_daily_feed(db, user_id, hours=24, limit=200, module=module)
            outcomes = feed.get("outcomes") or {}
            counts = feed.get("counts") or {}
            counts = {
                "interactions": int(counts.get("interactions") or 0),
                "agent_recent": int(counts.get("agent_recent") or 0),
                "coach_hints":  int(counts.get("coach_hints") or 0),
                "outcome_won":         int(outcomes.get("deals_won") or 0),
                "outcome_stage":       int(outcomes.get("stages_advanced") or 0),
                "outcome_first_touch": int(outcomes.get("first_touch") or 0),
            }
            ai_noticed = compute_ai_noticed(db, user_id, module)
            top_priority = _build_today_priority(db, user_id, 5, module=module)

            # Outcome-event records (specifically what was won today —
            # gives the LLM concrete records to mention).
            won_records: List[Dict[str, Any]] = []
            for it in feed.get("items") or []:
                if (it.get("item_type") == "outcome_event"
                        and it.get("event_type") == "deal.won"):
                    tgt = it.get("target") or {}
                    won_records.append({
                        "name": tgt.get("name") or "",
                        "value": it.get("value") or 0,
                    })
                    if len(won_records) >= 5:
                        break
    except Exception:
        logger.exception("list_brief_aggregate_failed user=%s", user_id)
        src_holder["v"] = "aggregate_error"
        raise HTTPException(500, "list_brief_failed")

    today_outcomes = {"won_records": won_records}
    summary_facts = {
        "module": module,
        "user_label": str(user_id),
        "counts": counts,
    }
    signature = compute_signature(
        user_id=user_id, module=module, today=today_local, counts=counts,
    )

    # Today's coach.proposal cards (read straight from gateway_actions
    # via the canonical reader). The CopilotListBrief surface renders
    # these as actionable "📋 复制话术" rows on the right rail — the
    # main user-visible signal that the morning cron actually ran.
    coach_cards: List[Dict[str, Any]] = []
    try:
        with db_session() as db:
            from hermes.crm.insights.coach_cards import read_coach_proposals
            coach_cards = read_coach_proposals(
                db, user_id, unread_only=False, module=module, limit=5,
            )
    except Exception:
        logger.exception("list_brief_coach_cards_failed user=%s", user_id)
        coach_cards = []

    base_payload = {
        "module": module,
        "ai_noticed": ai_noticed,
        "counts": counts,
        "coach_cards": coach_cards,
    }

    # Fast path: cache hit.
    try:
        with db_session() as db:
            cached = get_cached_brief(
                db, user_id=user_id, module=module, signature=signature,
            )
    except Exception:
        cached = None
    if cached:
        src_holder["v"] = "llm_cached"
        return {
            **base_payload,
            "summary": cached.get("summary") or "",
            "suggestions": cached.get("suggestions") or [],
            "source": "llm_cached",
        }

    # Cache miss: spawn bg task (single-flight per key); race against 3s.
    gen_task = _spawn_or_join_generation(
        ("list_brief", user_id, (module or "").strip(), signature),
        lambda: _generate_brief_and_cache(
            user_id=user_id, module=module, signature=signature,
            summary_facts=summary_facts,
            ai_noticed=ai_noticed,
            top_priority=top_priority,
            today_outcomes=today_outcomes,
            app=app,
        ),
    )
    try:
        brief = await asyncio.wait_for(asyncio.shield(gen_task), timeout=3.0)
    except asyncio.TimeoutError:
        brief = None

    if brief:
        src_holder["v"] = "llm_fresh"
        return {
            **base_payload,
            "summary": brief.get("summary") or "",
            "suggestions": brief.get("suggestions") or [],
            "source": "llm_fresh",
        }

    # Template fallback (also serves as the "still pending" stand-in).
    fb_summary = _fallback_summary(counts, ai_noticed, top_priority)
    fb_suggestions = _fallback_suggestions(module, top_priority, ai_noticed)
    src = "llm_pending" if not gen_task.done() else "scanner_template"
    src_holder["v"] = src
    return {
        **base_payload,
        "summary": fb_summary,
        "suggestions": fb_suggestions,
        "source": src,
    }


# The ``_fallback_summary`` / ``_fallback_suggestions`` cold-cache
# template helpers moved to ``hermes.crm.insights.priority``.


# Every celery beat task writes to ``cron_heartbeats`` via signals in
# celery_app.py (_heartbeat_prerun / _heartbeat_postrun). This table is
# the authoritative liveness source — previously /cron_health proxied
# off MAX(gateway_actions.created_at), which silently missed any cron
# that didn't emit gateway actions (weekly digest, retention sweeps,
# memory ingest). The list below mirrors celery_app.beat_schedule.
# Source: "celery" → read cron_heartbeats. "hermes_cron" → read
# hermes' per-profile jobs.json last_run_at across all profiles.
_CRON_HEALTH_JOBS: List[Dict[str, Any]] = [
    {"name": "crm-daily-coach", "label": "今日 Coach",
     "expected_interval_min": 24 * 60, "source": "hermes_cron"},
    {"name": "crm-weekly-digest", "label": "周报推送",
     "expected_interval_min": 7 * 24 * 60, "source": "hermes_cron"},
    {"name": "memory-ingest-recent", "label": "记忆收纳",
     "expected_interval_min": 5, "source": "celery"},
    {"name": "agent-messages-retention", "label": "会话清理",
     "expected_interval_min": 24 * 60, "source": "celery"},
    {"name": "audit-log-retention", "label": "审计归档",
     "expected_interval_min": 30 * 24 * 60, "source": "celery"},
]


def _hermes_cron_last_run(job_name: str, hermes_home_root: Optional[str] = None) -> Optional[datetime]:
    """Scan all hermes profile cron/jobs.json files; return the most
    recent ``last_run_at`` across all profiles for the named job.
    Returns None if no profile has run this job yet."""
    from pathlib import Path
    import json as _json
    base = Path(hermes_home_root) if hermes_home_root else Path.home() / ".hermes"
    profiles_root = base / "profiles"
    if not profiles_root.exists():
        return None
    most_recent: Optional[datetime] = None
    for entry in profiles_root.iterdir():
        if not entry.is_dir():
            continue
        jobs_path = entry / "cron" / "jobs.json"
        if not jobs_path.exists():
            continue
        try:
            with jobs_path.open() as f:
                doc = _json.load(f)
            for job in doc.get("jobs", []):
                if job.get("name") != job_name:
                    continue
                lra = job.get("last_run_at")
                if not lra:
                    continue
                dt = datetime.fromisoformat(lra)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if most_recent is None or dt > most_recent:
                    most_recent = dt
        except Exception:
            logger.exception("cron_health: jobs.json read failed %s", jobs_path)
    return most_recent


def _cron_status(minutes_since: Optional[float], expected_min: int) -> str:
    """green = within 1.5× expected, yellow = within 3×, red = beyond.
    None (never ran) = red so the user notices."""
    if minutes_since is None:
        return "red"
    if minutes_since <= expected_min * 1.5:
        return "green"
    if minutes_since <= expected_min * 3:
        return "yellow"
    return "red"


@router.get("/cron_health")
def cron_health_endpoint(request: Request):
    """Backend liveness panel for the SPA.

    Reads ``cron_heartbeats`` (written by celery task_prerun/postrun
    signals). One row per known beat task; status (green/yellow/red)
    folds in the expected cadence so the SPA can render colored chips
    without re-computing.

    ``last_status`` is the most recent run outcome: ``ok`` /
    ``error`` / ``started`` (still running) / ``never``. A ``red``
    status means the task hasn't run in 3× the expected interval —
    most likely celery beat is down.
    """
    _current_user_id(request)  # auth only — health is global
    out: List[Dict[str, Any]] = []
    try:
        with db_session() as db:
            for job in _CRON_HEALTH_JOBS:
                source = job.get("source", "celery")
                at: Optional[datetime] = None
                last_status = "never"
                last_duration_ms: Optional[int] = None
                last_detail: Any = None
                if source == "hermes_cron":
                    at = _hermes_cron_last_run(job["name"], _get_config(request).hermes_home_root)
                    if at is not None:
                        last_status = "ok"
                else:
                    row = None
                    try:
                        row = db.fetchone(
                            """
                            SELECT last_run_at, last_status,
                                   last_duration_ms, last_detail
                              FROM cron_heartbeats
                             WHERE name = %s
                            """,
                            (job["name"],),
                        )
                    except Exception:
                        row = None
                    if row:
                        at = row.get("last_run_at")
                        last_status = row.get("last_status") or "never"
                        last_duration_ms = row.get("last_duration_ms")
                        last_detail = row.get("last_detail")
                if at is not None:
                    delta_min = (
                        datetime.now(timezone.utc) - at
                    ).total_seconds() / 60.0
                else:
                    delta_min = None
                out.append({
                    "name": job["name"],
                    "label": job["label"],
                    "expected_interval_min": job["expected_interval_min"],
                    "source": source,
                    "last_run_at": at.isoformat() if at else None,
                    "minutes_since": (
                        round(delta_min, 1) if delta_min is not None else None
                    ),
                    "status": _cron_status(
                        delta_min, job["expected_interval_min"],
                    ),
                    "last_status": last_status,
                    "last_duration_ms": last_duration_ms,
                    "last_detail": last_detail,
                })
    except Exception:
        logger.exception("cron_health_failed")
        # Don't 500 — the SPA shows "—" gracefully; a 500 would make the
        # whole insights tab look broken.
    return {"jobs": out}


@router.get("/today_priority")
def today_priority_endpoint(
    request: Request, limit: int = 5, module: Optional[str] = None,
):
    """Urgency feed. ``module`` filters to one CRM module
    (accounts/leads/potentials/contacts) so per-module dashboards can
    show "🚨 客户·今天最该处理" etc; without ``module`` it's the
    cross-module global view used by /crm event-stream."""
    user_id = _current_user_id(request)
    limit = max(1, min(int(limit or 5), 20))
    try:
        with db_session() as db:
            items = _build_today_priority(db, user_id, limit, module=module)
    except Exception:
        logger.exception("today_priority_failed user=%s", user_id)
        raise HTTPException(500, "today_priority_failed")
    return {"items": items}


__all__ = ["router"]
