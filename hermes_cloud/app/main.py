"""
Hermes Cloud — internal company AI agent platform.

Architecture (profile mode):
  Each user runs their own `hermes gateway` process on a private port.
  This app handles auth, routing, and a thin proxy layer only.

  Client → /v1/chat/completions
         → authenticate
         → ensure_running(user_id) → start hermes process if needed
         → proxy to http://127.0.0.1:{port}/v1/chat/completions
         → return response
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import json
import logging
import os as _os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from hermes_cloud.app.auth import (
    clear_session_cookie,
    require_admin_auth,
    require_bearer_auth,
    require_dashboard_auth,
    set_session_cookie,
)
from hermes_cloud.app.auth_store import InMemoryAuthStore, SqlAlchemyAuthStore
from hermes_cloud.app.crm_auth_store import CrmUsersAuthStore
from hermes_cloud.app.crm_profile_store import CrmProfileStore
from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.config_store import InMemoryConfigStore, SqlAlchemyConfigStore
from hermes_cloud.app.hermes_patcher import PatchApplyError, ensure_hermes_patches_applied
# session_store substrate removed in charter v2 §定律 4 cleanup —
# agent_sessions (written by hermes via PgSessionDB patch 10) is the
# single source of truth for the ChatInbox.
from hermes_cloud.app.metrics import MetricsMiddleware, REGISTRY
from hermes_cloud.app import model_roles
from hermes_cloud.app.profile_manager import ProfileManager
from hermes_cloud.app.skill_policy import is_default_crm_skill, resolve_disabled
# Charter v2: SqlAlchemy / InMemory fallbacks removed; CrmProfileStore is
# the only profile_store implementation. See profile_store.py module docstring.
# Charter v2 Phase 3 (2026-05-06): RL subsystem (rl/adapter_store,
# rl/ab_router, rl/inference_client) was removed. The chat path now
# uses the per-profile model resolved by ``profile_model_client``
# directly — no A/B variant routing, no inference-server side
# adapter loading.
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)


# v3.3.1 — emit one info log the first time the flag-gate skips the
# Phase 3.1 dashboard short-circuit. Reset on process boot.
_V331_DASHBOARD_LOG_ONCE = False


_DEFAULT_TITLE = "New conversation"
_TITLE_MAX_LEN = 50

# Pure-greeting messages that shouldn't become the session title — the
# first *substantive* question does. Matched after whitespace/punctuation
# stripping + lowercasing. Kept intentionally small; anything longer than
# a bare greeting ("hello, can you help me...") is treated as substantive.
_GREETING_WORDS = frozenset({
    "你好", "您好", "嗨", "哈喽", "哈啰", "早", "早上好", "晚上好", "下午好",
    "在吗", "在不在", "在么",
    "hi", "hello", "hey", "yo", "hola",
    "ping", "test", "测试",
    "ok", "好的", "谢谢", "thanks", "thx",
})


def _is_greeting(text: str) -> bool:
    """Return True if ``text`` is a bare greeting / filler with no real
    question — those should be skipped when deriving a session title."""
    # Strip punctuation + whitespace, lowercase for ASCII comparisons.
    stripped = "".join(
        ch for ch in text
        if not ch.isspace() and ch not in "?!.,，。！？~～、:;：；…"
    ).lower()
    if not stripped:
        return True
    if stripped in _GREETING_WORDS:
        return True
    # Very short (<=3 chars) + no CJK question marker → likely filler.
    if len(stripped) <= 3 and "？" not in text and "?" not in text:
        return True
    return False


def _extract_user_text(m: dict) -> str:
    """Pull the text body out of one message row (OpenAI/Anthropic shapes)."""
    content = m.get("content")
    if isinstance(content, str):
        return " ".join(content.split()).strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return " ".join(" ".join(parts).split()).strip()
    return ""


def _normalize_manager_ids(raw: Any) -> list[int]:
    """请求体的 manager_ids → 去重的 int 列表(非数组/坏值容错为空)。"""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(out))  # 去重保序


def _filter_existing_user_ids(db: Any, ids: list[int]) -> list[int]:
    """只保留真实存在(未删除)的用户 id —— 防止把无效经理 id 写进 manager_ids。"""
    if not ids:
        return []
    rows = db.fetchall(
        "SELECT id FROM users WHERE id = ANY(%s) AND deleted = FALSE", (ids,))
    valid = {int(r["id"]) for r in (rows or [])}
    return [i for i in ids if i in valid]


def _derive_title_from_body(raw_body: bytes) -> str | None:
    """Extract the first *substantive* user message from a chat-completions
    request body and turn it into a session title.

    Skips bare greetings ("你好", "hi", "在吗", "ping"…) so the title
    reflects the user's real question. If every user message is a
    greeting we fall back to the last one rather than staying on the
    placeholder — some polish beats none.

    An LLM-generated summary (hermes.agent.title_generator) would be
    prettier but adds latency/cost and would need a write-back bridge
    into the cloud SessionStore — the raw first substantive message is
    "用户第一句核心问题" by definition.
    """
    try:
        import json as _json  # noqa: PLC0415
        body = _json.loads(raw_body)
        user_texts: list[str] = []
        for m in body.get("messages", []) or []:
            if m.get("role") != "user":
                continue
            text = _extract_user_text(m)
            if text:
                user_texts.append(text)
        if not user_texts:
            return None
        # Prefer the first substantive message; fall back to the last one
        # if everything is a greeting (still better than "New conversation").
        chosen = next((t for t in user_texts if not _is_greeting(t)), user_texts[-1])
        if len(chosen) > _TITLE_MAX_LEN:
            chosen = chosen[: _TITLE_MAX_LEN - 1].rstrip() + "…"
        return chosen
    except Exception:
        return None


def _crm_db_reachable() -> bool:
    """Probe the CRM PG pool with a trivial ``SELECT 1``.

    Used at boot to pick between :class:`CrmUsersAuthStore` (production
    path) and the legacy :class:`SqlAlchemyAuthStore` / in-memory store
    (dev fallback when the CRM DB isn't up yet). Failures are swallowed
    — a missing DB just means "fall back", never "crash the app".
    """
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception as exc:  # pragma: no cover — logged for triage
        logger.warning("CRM DB unreachable at boot: %s — using legacy auth store", exc)
        return False


# ---------------------------------------------------------------------------
# Fail-fast DB gates — Charter §六定律 6 (audit/reversibility) + §一定律 1
# (single source of truth) require that the cloud's identity-adjacent and
# CRM-business stores are PG. The "可启动优先" InMemory fallback ladder is
# explicitly designed to let the process boot when stores are unreachable so
# unit tests + UI shells stay simple. In any environment that intends to be
# real (anything past dev), a half-residue boot (process up, /api/me works,
# agents/audit/CRM all silently empty) is dangerous: operators can't tell
# from the outside that the deployment is broken.
#
# These gates make the server refuse to boot when its declared backends are
# not actually there. They run in ``_apply_failfast_gates`` from
# ``create_app()`` after config + crm probe but before any routes serve.
# ---------------------------------------------------------------------------


class FailFastDbError(RuntimeError):
    """Raised when the boot-time DB gates refuse to start the app."""


def _alembic_head_revision() -> Optional[str]:
    """Return the highest-numbered revision id under alembic/versions/, or
    None if the directory isn't readable. We compare the live DB's
    alembic_version against this so a deploy without ``alembic upgrade
    head`` warns loudly at boot."""
    try:
        from pathlib import Path as _Path
        # alembic/versions sits at the repo root, two levels up from this file
        # (hermes_cloud/app/main.py → hermes_cloud/ → repo root).
        repo_root = _Path(__file__).resolve().parents[2]
        versions = repo_root / "alembic" / "versions"
        if not versions.is_dir():
            return None
        # Filenames look like "0030_agent_session_metrics.py". Take the
        # numeric prefix; lexicographic max coincides with numeric max
        # because we 0-pad to 4 digits.
        ids = []
        for f in versions.glob("*.py"):
            stem = f.stem
            head = stem.split("_", 1)[0]
            if head.isdigit():
                ids.append(head)
        return max(ids) if ids else None
    except Exception:
        return None


def _apply_failfast_gates(cfg: AppConfig) -> None:
    """Refuse to start when the declared DBs aren't there.

    Gate 1 — session_store_url:
        - Set to ``postgres*`` but PG unreachable → fail. The user explicitly
          declared a PG deployment; a deferred query failure on first request
          is strictly worse than refusing at boot.
        - Unset + ``HERMES_CLOUD_REQUIRE_DB=1`` → fail. Production deployments
          opt in by setting the env var; without it (test/dev default), we
          keep the InMemory ladder for backwards compatibility.

    Gate 2 — CRM DB:
        - ``HERMES_CRM_DATABASE_URL`` (or ``CRM_DATABASE_URL``) explicitly
          set but ``_crm_db_reachable()`` returns False → fail. Dev path
          where neither var is set keeps the localhost fallback that
          ``crm.db._resolve_dsn`` synthesizes; if that turns out to also be
          unreachable, the warning fires and the legacy auth store ladder
          handles it (this is the only "可启动优先" path that survives).

    Gate 3 — schema version:
        - ``alembic_version.version_num`` doesn't match the highest
          revision in ``alembic/versions/`` → log a loud warning. We do
          NOT fail here: a forward-only deploy that lands code before
          ``alembic upgrade`` is normal in some workflows. But the warning
          puts the gap on someone's screen the moment the server starts.

    Gate 4 — InMemory shadow ban under REQUIRE_DB:
        - With ``HERMES_CLOUD_REQUIRE_DB=1``, no store may resolve to an
          InMemory* class. (This is enforced by Gates 1 + 2 as a
          consequence — Gate 4 is the explicit assertion at end of boot
          to catch a future refactor that sneaks an InMemory back in.)
    """
    require_db = (
        _os.environ.get("HERMES_CLOUD_REQUIRE_DB", "").strip().lower()
        in ("1", "true", "yes", "on")
    )

    # ---- Gate 1: session_store_url --------------------------------------
    sess_url = (cfg.session_store_url or "").strip()
    if sess_url.startswith("postgres"):
        try:
            from sqlalchemy import create_engine, text
            from hermes_cloud.app.db import _make_engine_kwargs as _eng_kw  # noqa: F401
        except Exception:
            from sqlalchemy import create_engine, text  # type: ignore
        try:
            engine = create_engine(sess_url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise FailFastDbError(
                f"Gate 1: HERMES_CLOUD_DB_URL is set to {sess_url!r} but the "
                f"database is unreachable: {exc}. Refusing to start with the "
                f"legacy SQLAlchemy fallback because half-residue boot hides "
                f"production breakage. Fix the DB or unset the env var to "
                f"opt into dev mode."
            ) from exc
    elif require_db:
        raise FailFastDbError(
            "Gate 1: HERMES_CLOUD_REQUIRE_DB=1 but session_store_url is "
            "unset or non-postgres. Production deployments must declare "
            "their DB backend explicitly."
        )

    # ---- Gate 2: CRM DB --------------------------------------------------
    crm_url_explicit = bool(
        _os.environ.get("HERMES_CRM_DATABASE_URL")
        or _os.environ.get("CRM_DATABASE_URL")
    )
    if crm_url_explicit and not _crm_db_reachable():
        raise FailFastDbError(
            "Gate 2: HERMES_CRM_DATABASE_URL is set but the CRM PG cluster "
            "is unreachable. Refusing to start — a CRM-down boot would "
            "silently empty agents/inbox/audit. Fix the DB connection or "
            "unset the env var to fall back to the localhost dev DSN."
        )
    if require_db and not _crm_db_reachable():
        raise FailFastDbError(
            "Gate 2: HERMES_CLOUD_REQUIRE_DB=1 but CRM PG is unreachable."
        )

    # ---- Gate 3: schema version warning ---------------------------------
    # Skip when CRM is unreachable in dev mode (Gate 2 already let us
    # through; trying to query alembic_version would just fail again).
    if _crm_db_reachable():
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                db_version = db.fetchcolumn(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                )
            head = _alembic_head_revision()
            if head and db_version and not str(db_version).startswith(head):
                logger.warning(
                    "Gate 3: alembic_version=%r but versions/ head is %r — "
                    "run ``alembic upgrade head`` before serving traffic.",
                    db_version, head,
                )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Gate 3: alembic version probe failed: %s", exc)


def _assert_no_inmemory_shadow(state: Any) -> None:
    """Gate 4 — under HERMES_CLOUD_REQUIRE_DB=1, refuse to expose any
    InMemory* store via app.state. Caught after store wiring so a future
    refactor that sneaks one back in doesn't go unnoticed."""
    if _os.environ.get("HERMES_CLOUD_REQUIRE_DB", "").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        return
    leaks: list[str] = []
    for name in ("auth_store", "config_store",
                 "profile_store", "adapter_store"):
        store = getattr(state, name, None)
        cls = type(store).__name__ if store is not None else "<missing>"
        if cls.startswith("InMemory"):
            leaks.append(f"{name}={cls}")
    if leaks:
        raise FailFastDbError(
            f"Gate 4: HERMES_CLOUD_REQUIRE_DB=1 but stores resolved to "
            f"InMemory backends: {leaks}. This means Gates 1-2 are "
            f"misconfigured or the store-selection ladder has drifted."
        )


# ---------------------------------------------------------------------------
# Effective-config resolvers for the sync step.
#
# ``config_store`` deliberately does NOT fall back from a non-default profile
# to the user's default profile — the pinning test
# ``test_non_default_profile_returns_none_when_unset`` guards that invariant
# so UI reads of an unconfigured profile don't silently shadow the default
# config (which would make "save" vs "inherit" indistinguishable).
#
# But at the **sync** step — where we materialize cloud config into the
# profile's ``config.yaml`` for a hermes subprocess that is about to boot —
# we need *some* model. Otherwise hermes calls the upstream LLM with
# ``model=""`` and returns a 400 ("model: String should have at least 1
# character" on anthropic, "model must be a non-empty string" on
# openai-codex), which the feishu bridge surfaces to the user as "(空回复)".
#
# So the fallback policy lives here, explicitly, at the sync layer only.
# ---------------------------------------------------------------------------


def _model_cfg_is_populated(cfg) -> bool:
    """A model config row is 'populated' iff it has at least one of the
    core fields. An all-None row is as good as missing."""
    return bool(cfg and (cfg.model or cfg.api_key or cfg.base_url))


def _resolve_effective_model_cfg(store, user_id: str, profile_id: str | None):
    """Return the :class:`UserModelConfig` a profile should boot with.

    If *profile_id* refers to a non-default profile that has no populated
    model row of its own, fall back to the user's default-profile row.
    Returns ``None`` only when neither is configured.
    """
    target_id = profile_id if profile_id is not None else user_id
    own = store.get_user_model(user_id, profile_id=target_id)
    if _model_cfg_is_populated(own):
        return own
    if target_id != user_id:
        default_cfg = store.get_user_model(user_id)
        if _model_cfg_is_populated(default_cfg):
            return default_cfg
    return own  # may be None or an all-None row; caller decides


def _toolsets_cfg_is_populated(cfg) -> bool:
    return bool(cfg and cfg.toolsets)


def _resolve_effective_toolsets_cfg(store, user_id: str, profile_id: str | None):
    """Toolsets twin of :func:`_resolve_effective_model_cfg`."""
    target_id = profile_id if profile_id is not None else user_id
    own = store.get_user_toolsets(user_id, profile_id=target_id)
    if _toolsets_cfg_is_populated(own):
        return own
    if target_id != user_id:
        default_cfg = store.get_user_toolsets(user_id)
        if _toolsets_cfg_is_populated(default_cfg):
            return default_cfg
    return own


# Charter v2 Phase 3 (2026-05-06): the @<specialist> prefix
# short-circuit was removed when the last specialists (research /
# analyst) were folded into markdown skills. Dashboard chat input
# now flows unmodified through the per-profile hermes subprocess;
# the main agent reads ``skill_view(name="research")`` etc. instead
# of relying on a magic prefix.


def _latest_user_text_from_body(raw_body: bytes) -> str:
    """Best-effort extract the most recent user message text from a
    chat-completions request body. Returns empty string on any error."""
    if not raw_body:
        return ""
    try:
        b = json.loads(raw_body)
    except Exception:
        return ""
    msgs = b.get("messages") or []
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(
                str(p.get("text") or "")
                for p in c
                if isinstance(p, dict) and p.get("type") == "text"
            )
    return ""


def _build_chat_completion_response(
    *, content: str, model: str, stream: bool, draft_id: int | None = None,
):
    """Wrap a synthesized text payload as either a non-streaming OpenAI
    chat completion JSON, or an SSE stream that emits the entire content
    as a single delta chunk followed by ``[DONE]`` (and a trailing
    ``hermes.draft.id`` event when known)."""
    chat_id = f"chatcmpl-specialist-{uuid.uuid4().hex[:12]}"
    created = int(dt.datetime.utcnow().timestamp())
    if not stream:
        return JSONResponse({
            "id": chat_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
        })

    async def _gen():
        # Initial role chunk
        first = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"},
                         "finish_reason": None}],
        }
        yield f"data: {json.dumps(first)}\n\n".encode("utf-8")
        # Single content chunk
        body = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": content},
                         "finish_reason": None}],
        }
        yield f"data: {json.dumps(body)}\n\n".encode("utf-8")
        # Final stop chunk
        last = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(last)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"
        if draft_id is not None:
            yield (
                "event: hermes.draft.id\n"
                f"data: {json.dumps({'draft_id': int(draft_id)})}\n\n"
            ).encode("utf-8")

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _maybe_gateway_command_short_circuit(
    *, raw_body: bytes, user_id: str, profile_id: str, stream: bool,
):
    """Detect a gateway text command (``撤销 <id>`` / ``强制撤销 <id>`` /
    ``通过 <id>`` / ``拒绝 <id>`` / ``改 <id> ...``) typed straight into
    the dashboard chat box. On match, route to the existing text_commands
    parser with ``via_channel='webui'`` and return a chat-completion
    response carrying the parser's reply text. Phase 4.3 — sales hint
    text in the markdown renderer says ``回 "撤销 <id>"`` so this path
    must actually do something when a dashboard user types it.
    """
    text = _latest_user_text_from_body(raw_body)
    if not text:
        return None
    try:
        from hermes.action_gateway.text_commands import parse_command
        parsed = parse_command(text.strip())
    except Exception:
        return None
    if parsed is None:
        return None

    # Resolve uid + db lazily so a malformed user_id falls through to the
    # proxy instead of 500-ing the chat endpoint.
    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        return None

    try:
        from hermes.crm.db import db_session
        from hermes.action_gateway.text_commands import try_gateway_command
    except Exception:
        return None

    try:
        with db_session() as db:
            reply = try_gateway_command(
                text.strip(), user_id=uid_int, via_channel="webui", db=db,
            )
    except Exception as exc:
        logger.warning("gateway_command_short_circuit_failed err=%r", exc)
        return None

    if reply is None:
        return None
    return _build_chat_completion_response(
        content=reply, model="gateway:command", stream=stream,
    )


def _maybe_specialist_short_circuit(
    *, raw_body: bytes, user_id: str, profile_id: str, stream: bool,
):
    """No-op stub — the @<specialist> dashboard prefix was removed in
    Charter v2 Phase 3 (2026-05-06). Always returns None so callers
    fall through to the normal chat-completions proxy path.
    """
    return None


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or AppConfig()

    # Charter §六定律 6 / §一定律 1 fail-fast gates. See _apply_failfast_gates
    # for the detailed rationale; in short: a half-residue boot (process up,
    # /api/me works, agents/audit/CRM silently empty) hides production
    # breakage. Gates 1-2 raise FailFastDbError before any route serves;
    # Gate 3 warns; Gate 4 runs after store wiring (further down).
    _apply_failfast_gates(cfg)

    # ------------------------------------------------------------------
    # ProfileManager + httpx client (shared for all proxied requests)
    # ------------------------------------------------------------------
    hermes_home_root = Path(cfg.hermes_home_root) if cfg.hermes_home_root else None
    profile_manager = ProfileManager(
        internal_key=cfg.internal_key,
        port_range_start=cfg.port_range_start,
        port_range_end=cfg.port_range_end,
        idle_timeout=cfg.idle_timeout_seconds,
        hermes_bin=cfg.hermes_bin,
        hermes_home_root=hermes_home_root,
        # Charter §一原语 ① — agent message tap. Subprocesses POST agent
        # messages to ${CLOUD_INTERNAL_URL}/internal/agent/event so they
        # land in the canonical interactions table. CLOUD_INTERNAL_URL is
        # the same env var insights/notify.py uses for coach pushes; we
        # mirror its loopback default so dev runs work without setup.
        agent_event_url=(
            (_os.environ.get("CLOUD_INTERNAL_URL") or "http://127.0.0.1:8765")
                .rstrip("/") + "/internal/agent/event"
        ),
        # Same base URL — gateway/platforms/* append the
        # ``/api/touchpoints/internal/ingest/<src>`` suffix themselves
        # so both inbound (and, for email, outbound) messages land in
        # ``interactions`` for the SPA ChatBox.
        interaction_log_url=(
            _os.environ.get("CLOUD_INTERNAL_URL") or "http://127.0.0.1:8765"
        ),
        # Authoritative profile_id → cloud user_id lookup for eager
        # spawn. Late-bound closure: app.state.profile_store is
        # populated before ProfileManager.startup() runs. Without this
        # eager-spawn falls back to profile_id, poisoning CRM_USER_ID.
        user_id_resolver=lambda pid: (
            (lambda p: str(p.user_id) if p is not None else None)(
                app.state.profile_store.get_profile(pid)
            )
            if getattr(app.state, "profile_store", None) is not None
            else None
        ),
    )
    # ``max_keepalive_connections=0`` forces a fresh TCP for every
    # request. Costs ~10ms per call but eliminates stale-connection
    # ReadTimeout: long-running dispatch calls (4+ min agent runs)
    # leave the underlying socket idle past macOS's keepalive timer,
    # and the OS silently drops it; the next request would then reuse
    # that dead connection from the pool and instantly raise
    # ReadTimeout('') with no diagnostics. We hit this with
    # ``crm-daily-coach`` skill calls — see 2026-05-15 debugging
    # session in docs/2026-05-15-coach-remediation-charter-v2.md.
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Apply local hermes patches before anything touches the submodule.
        # The ``hermes`` submodule points at upstream, so we cannot bump its
        # pointer to include our own fixes (e.g. the gateway contextvars bug
        # fix); we vendor them as patch files and reapply on every boot.
        _apply_hermes_patches_or_raise(cfg)

        # Charter v2: all CRM skills live natively under
        # ``hermes/skills/`` and are picked up by hermes' built-in
        # skill resolver + sync_skills() manifest sync (which copies
        # each into ``~/.hermes/profiles/<id>/skills/`` on subprocess
        # launch, preserving user self-improvement edits via
        # ``.bundled_manifest`` hash tracking).
        #
        # Current bundled CRM set (13 skills, patches 07/24/28/37/40/
        # 41/46/47/50):
        #   - interactive: crm-intake, crm-email-drafting,
        #     crm-compliance-check, crm-coach, crm-research, crm-analyst,
        #     crm-validation-error, crm-line-items
        #   - utility (cloud invokes via run_utility_skill): crm-daily-brief,
        #     crm-orphan-commitment, crm-question-suggest, crm-decisions-context
        #   - cron (hermes native cron triggers): crm-daily-coach,
        #     crm-weekly-digest
        # The cloud-side ``system_skills_bootstrap`` mirror was
        # removed — hermes' own sync handles seed+update with proper
        # user-edit preservation.

        # Charter v2 Phase 3 (2026-05-06): all specialist runtime
        # registration / cloud delegate resolver wiring / specialist
        # ACL seeding has been removed along with the agents/
        # subsystem. The main agent now handles previously-delegated
        # work itself, guided by skill markdown.

        app.state.profile_manager = profile_manager
        app.state.http_client = http_client

        # Charter v2 Phase 3: RL signal_writer removed. ``signal_writer``
        # state attribute pinned to None for any defensive getattr
        # callers that survive the cleanup.
        app.state.signal_writer = None

        await profile_manager.startup()

        # Warm-boot every active profile so per-profile scheduled tasks
        # (cronjobs / heartbeats living in each profile's scheduler.db) fire
        # from the moment the server comes up — NOT lazy-started on first HTTP.
        asyncio.create_task(_boot_all_profile_processes(app))

        # ------------------------------------------------------------------
        # Periodic tasks — Celery only.
        #
        # Scheduling lives entirely in celery_app.beat_schedule; the
        # supervisor script runs `celery worker` + `celery beat`
        # alongside uvicorn. The previous in-process asyncio fallback
        # was removed 2026-05-18 because it silently covered only 2
        # of 7 periodic tasks, so any deployment without celery was
        # missing 5 jobs without warning.
        # ------------------------------------------------------------------
        if _crm_up:
            if not _os.environ.get("CELERY_BROKER_URL"):
                logger.error(
                    "scheduler: CELERY_BROKER_URL not set — periodic "
                    "tasks WILL NOT RUN. Set it in .env (e.g. "
                    "redis://localhost:6379/0) and ensure the "
                    "supervisor is running celery worker + beat."
                )
            else:
                logger.info(
                    "scheduler: Celery configured — periodic tasks "
                    "managed by celery beat (see scripts/run_cloud_supervised.sh)"
                )

        # ------------------------------------------------------------------
        # Mail poller — per-user IMAP pull → Interaction Log.
        # Gated by HERMES_IMAP_POLL=1 so dev servers don't immediately hit
        # user-configured IMAP hosts on boot.
        # ------------------------------------------------------------------
        if _crm_up and _os.environ.get("HERMES_IMAP_POLL") == "1" and getattr(app.state, "mail_store", None):
            try:
                from hermes_cloud.app.mail.poller import run_poller
                from hermes_cloud.app.touchpoints.base import ingest as _tp_ingest

                _imap_stop = asyncio.Event()
                app.state.mail_poller_stop = _imap_stop
                app.state.mail_poller_task = asyncio.create_task(run_poller(
                    store=app.state.mail_store,
                    ingest_fn=_tp_ingest,
                    secret=cfg.internal_key,
                    stop_event=_imap_stop,
                ))
                logger.info("mail_poller: started (HERMES_IMAP_POLL=1)")
            except Exception:
                logger.exception("mail_poller: failed to start")

        yield

        # Mail poller shutdown
        _imap_stop = getattr(app.state, "mail_poller_stop", None)
        _imap_task = getattr(app.state, "mail_poller_task", None)
        if _imap_stop is not None:
            _imap_stop.set()
        if _imap_task is not None:
            try:
                await asyncio.wait_for(_imap_task, timeout=5.0)
            except Exception:
                pass

        # Charter v2 Phase 3 (2026-05-06): RL async signal writer
        # removed; nothing to drain on shutdown.
        await profile_manager.shutdown()
        await http_client.aclose()

    def _apply_hermes_patches_or_raise(cfg: AppConfig) -> None:
        if _os.environ.get("HERMES_CLOUD_SKIP_PATCHES") == "1":
            logger.warning("HERMES_CLOUD_SKIP_PATCHES=1 set; not applying hermes patches")
            return
        repo_root = Path(__file__).resolve().parents[2]
        hermes_dir = repo_root / "hermes"
        patches_dir = repo_root / "hermes-patches"
        try:
            ensure_hermes_patches_applied(hermes_dir, patches_dir)
        except PatchApplyError as exc:
            # Fail loudly rather than run on a half-patched hermes.
            logger.error("hermes patch apply failed: %s", exc)
            raise

    async def _boot_all_profile_processes(app: FastAPI):
        """启动时为每个 active profile 拉起 hermes 子进程。

        为什么每个 profile 都要启（而不是 lazy start）：每个 profile 的 scheduler.db
        定时任务 / heartbeat 是在 profile 自己的 hermes 子进程里调度的 —— 子进程不
        跑，cron 就不会触发。这是多 profile 路径的硬约束。

        流程：
          1. 遍历 auth_store 里所有 active user，为每个用户自动补建默认 profile
             （对老用户零感知升级，等价于 Task 8 auto-backfill）。
          2. 调 profile_store.list_all_active_profiles() 拿到所有 profile（含非默认）。
          3. 对每个 profile 同步 config.yaml 并 ensure_running。
        """
        await asyncio.sleep(2)  # 等服务器绑端口再 spawn 子进程
        auth_store = app.state.auth_store
        profile_store = app.state.profile_store

        users = auth_store.list_users() if hasattr(auth_store, "list_users") else []
        active_users = [u for u in users if u.is_active]

        # Auto-backfill: 每个 active user 至少有一条默认 profile 记录
        for user in active_users:
            try:
                profile_store.ensure_default_profile(user_id=user.id)
            except Exception as exc:
                logger.warning(
                    "boot: ensure_default_profile failed uid=%s: %s", user.id, exc
                )

        profiles = profile_store.list_all_active_profiles()
        if not profiles:
            return
        logger.info("boot: starting processes for %d active profiles", len(profiles))
        for profile in profiles:
            try:
                try:
                    _sync_config_to_profile(profile.user_id, profile.id)
                except Exception as exc:
                    logger.warning(
                        "boot: config sync failed uid=%s pid=%s: %s",
                        profile.user_id, profile.id, exc,
                    )
                await app.state.profile_manager.ensure_running(
                    profile.id, user_id=profile.user_id
                )
                logger.info(
                    "boot: started process uid=%s pid=%s name=%s",
                    profile.user_id, profile.id, profile.name,
                )
            except Exception as exc:
                logger.error(
                    "boot: failed to start process uid=%s pid=%s err=%s",
                    profile.user_id, profile.id, exc,
                )

    app = FastAPI(title=cfg.service_name, lifespan=lifespan)
    app.add_middleware(MetricsMiddleware)

    # Locale resolution — populates the ``current_locale`` ContextVar from
    # ``?locale=`` / ``Accept-Language`` / session so CRM i18n helpers
    # (``localize_field_label``, ``t``) return the caller's language
    # instead of the permanent ``zh-CN`` default.
    #
    # Registered BEFORE SessionMiddleware on purpose. Starlette's
    # ``add_middleware`` prepends to ``user_middleware`` and then the
    # list is reversed when wrapping, so the *last* registered middleware
    # becomes the *outermost* and runs first. Registering I18n first makes
    # it innermost, so SessionMiddleware has already populated
    # ``scope["session"]`` by the time our middleware reads it.
    from hermes_cloud.app.i18n_middleware import I18nMiddleware
    app.add_middleware(I18nMiddleware)

    # Session middleware for CRM server-rendered pages (cookie-based auth).
    from starlette.middleware.sessions import SessionMiddleware
    _session_secret = _os.environ.get("HERMES_SESSION_SECRET", cfg.session_cookie_secret or "hermes-dev-session-secret")
    app.add_middleware(SessionMiddleware, secret_key=_session_secret)
    # Ensure the signed `hc_session` HttpOnly cookie (used by the SPA and
    # dashboard) always has a key to sign with. Without this, login
    # endpoints would silently skip cookie issuance and the SPA couldn't
    # authenticate. Unified with SessionMiddleware's secret so operators
    # only set HERMES_SESSION_SECRET in one place.
    # AppConfig is a frozen dataclass; backfill the resolved session
    # secret via object.__setattr__ rather than rebuilding the config
    # (which would strip subclass state or custom attributes added
    # elsewhere during boot).
    import dataclasses as _dc
    if not cfg.session_cookie_secret:
        cfg = _dc.replace(cfg, session_cookie_secret=_session_secret)
    # Expose the resolved config on app.state so request handlers (CRM
    # login, bridge identity) can read `session_cookie_secret` without
    # reaching back into the module closure.
    app.state.config = cfg

    # Charter v2 Phase 3 (2026-05-06): RL prometheus collectors
    # (signal_writer / dlq) removed along with the rl/ subsystem.

    # ------------------------------------------------------------------
    # Stores
    # ------------------------------------------------------------------
    # Phase 1c.2: prefer the unified CRM ``users`` table as the auth
    # backend so one login serves both ``/crm/*`` and the legacy dashboard
    # (plan §4.1, decisions D1–D5). The selection ladder is keyed on the
    # ``session_store_url`` scheme so each deployment shape keeps its
    # historical isolation semantics:
    #
    #   postgresql://...  + CRM reachable   → CrmUsersAuthStore
    #   postgresql://...  + CRM unreachable → SqlAlchemyAuthStore (legacy)
    #   sqlite:///...                       → SqlAlchemyAuthStore  (per-tmpfile dev isolation)
    #   session_store_url unset             → InMemoryAuthStore    (unit-test default)
    #
    # The scheme gate matters: dev runs (dev_server.py) default to a
    # sqlite session DB and several tests (test_dev_server,
    # test_me_api, ...) rely on that giving them an isolated auth store
    # rather than the shared CRM ``users`` rows.
    _pg_url = (
        cfg.session_store_url
        and cfg.session_store_url.startswith("postgres")
    )
    _crm_up = bool(_pg_url) and _crm_db_reachable()

    if _pg_url:
        app.state.auth_store = (
            CrmUsersAuthStore()
            if _crm_up
            else SqlAlchemyAuthStore(cfg.session_store_url)
        )
    elif cfg.session_store_url:
        app.state.auth_store = SqlAlchemyAuthStore(cfg.session_store_url)
    else:
        app.state.auth_store = InMemoryAuthStore()
    for token, user_id in cfg.api_keys.items():
        app.state.auth_store.seed_user_key(token=token, user_id=user_id)

    # Phase 1c.4: config / session / channel stores follow the same
    # scheme-gated ladder — when CRM PG is reachable, hand them the CRM
    # SA URL so every identity-adjacent table shares one database.
    # Dev (sqlite) and unset URLs keep their historical isolation.
    _store_url = cfg.session_store_url
    if _crm_up:
        from hermes.crm.db import resolve_crm_sa_url

        _store_url = resolve_crm_sa_url()

    app.state.config_store = (
        SqlAlchemyConfigStore(_store_url)
        if _store_url
        else InMemoryConfigStore()
    )
    from pathlib import Path as _Path
    _hermes_home = _Path(cfg.hermes_home_root) if cfg.hermes_home_root else _Path.home() / ".hermes"

    # Charter v2 §一 "agent is core" — default per-profile hermes cron
    # jobs (daily-coach, weekly-digest) live in
    # ~/.hermes/profiles/<id>/cron/jobs.json. Backfill on boot so existing
    # profiles get the defaults without manual provisioning. Idempotent
    # (won't duplicate; won't overwrite user customizations).
    try:
        from hermes_cloud.app.cron_provisioner import backfill_all_profiles
        _prov = backfill_all_profiles(_hermes_home)
        if _prov.get("added_total") or _prov.get("updated_total"):
            logger.info("cron_provisioner backfill: %s", _prov)
    except Exception:
        logger.exception("cron_provisioner: backfill failed")
    # Charter v2 §定律 4 — session_store removed; agent_sessions
    # (written by hermes via PgSessionDB patch 10) is the single
    # source of truth. /api/sessions queries it directly.
    # Charter v2 §简化原则: PG is the only supported backend for
    # profiles. Fail-fast if PG isn't reachable — dropping into a
    # SQLite/in-memory fallback masks misconfigured deployments and
    # creates "ghost" data that doesn't survive restart.
    #
    # Tests stub psycopg via conftest, so ``_crm_up`` is False there;
    # they don't reach this code path with PG actually running. To
    # keep the test suite alive without re-introducing fallbacks,
    # leave ``app.state.profile_store`` UNSET when PG is unreachable
    # AND the app config has no api_keys (the marker the test fixtures
    # use to spin up an isolated AppConfig). Tests that need a profile
    # store must inject one explicitly via ``app.state.profile_store
    # = ...``. Production code paths configure HERMES_CRM_DATABASE_URL
    # and never hit this branch.
    if _pg_url and _crm_up:
        app.state.profile_store = CrmProfileStore()
    elif _pg_url and not _crm_up:
        # Real env declares PG but it's unreachable — that's a misconfig
        # we want to surface immediately.
        raise RuntimeError(
            "Charter v2: profiles require a reachable PG via "
            "HERMES_CRM_DATABASE_URL. SqlAlchemy / InMemory fallbacks "
            "are removed."
        )
    else:
        # Test mode: no PG URL set. Tests must inject profile_store
        # manually if their assertion path needs it.
        app.state.profile_store = None  # type: ignore[assignment]
        logger.warning(
            "profile_store unset (no HERMES_CRM_DATABASE_URL) — "
            "fixture-only path. Production must set the env var."
        )

    # Mail account store (IMAP receive + SMTP send, one row per user).
    # Shares the primary SQL URL so rows live next to auth tables.
    try:
        from hermes_cloud.app.mail.store import SqlAlchemyMailStore
        _mail_url = cfg.session_store_url
        # psycopg2 isn't installed; codebase uses psycopg3 everywhere.
        # Normalize the SQLAlchemy driver prefix so create_engine picks
        # the psycopg3 dialect.
        if isinstance(_mail_url, str):
            if _mail_url.startswith("postgresql+psycopg2://"):
                _mail_url = "postgresql+psycopg://" + _mail_url[len("postgresql+psycopg2://"):]
            elif _mail_url.startswith("postgres://"):
                _mail_url = "postgresql+psycopg://" + _mail_url[len("postgres://"):]
            elif _mail_url.startswith("postgresql://"):
                _mail_url = "postgresql+psycopg://" + _mail_url[len("postgresql://"):]
        app.state.mail_store = SqlAlchemyMailStore(_mail_url)
    except Exception:
        logger.exception("mail: store init failed")
        app.state.mail_store = None

    # Charter v2 Phase 3 (2026-05-06): RL adapter store / A/B router /
    # inference-server control client all removed. The chat path uses
    # the per-profile model resolved by ``profile_model_client``
    # directly. State attributes are pinned to None so the few
    # callsites that still ``getattr(app.state, "...", None)`` no-op
    # cleanly without a runtime AttributeError.
    app.state.adapter_store = None
    app.state.ab_router = None
    app.state.router_stats = None
    app.state.inference_client = None
    app.state.rl_guard_default = False

    # Gate 4: under HERMES_CLOUD_REQUIRE_DB=1, refuse to expose any
    # InMemory* store. Catches a future refactor that sneaks one back in.
    _assert_no_inmemory_shadow(app.state)

    # ------------------------------------------------------------------
    # Exception handler: flatten detail so errors surface at top level
    # ------------------------------------------------------------------
    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            content = detail
        else:
            content = {"error": {"message": str(detail), "type": "api_error", "code": "error"}}
        return JSONResponse(status_code=exc.status_code, content=content)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _parse_json_body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "Request body must be valid JSON.",
                                  "type": "invalid_request_error", "code": "invalid_json"}},
            )

    def _require_internal_key(request: Request) -> None:
        """Charter v2 §一系统：同机器内部进程互调（celery worker → web、
        hermes 子进程 → web、CLI 工具 → web）不再装外部礼仪。
        loopback (127.0.0.1 / ::1) 直接放行，远端调用仍需 key。"""
        peer = (request.client.host if request.client else "") or ""
        if peer in ("127.0.0.1", "::1", "localhost"):
            return
        secret = cfg.channel_bind_webhook_secret or cfg.internal_key
        provided = request.headers.get("X-Internal-Key", "")
        if not hmac.compare_digest(provided, secret):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Invalid internal key.",
                                  "type": "auth_error", "code": "invalid_internal_key"}},
            )

    def _resolve_profile_id(request: Request, user_id: str) -> str:
        """Resolve the target profile for this request.

        Rules (see docs/2026-04-15-multi-profile-foundation.md):
          - If ``X-Profile-Id`` header is present, look it up in the store.
            It MUST exist, belong to *this* user, and be active — otherwise 403.
          - If absent, fall back to the user's default profile. If the user
            has no default profile row yet (legacy user), create one lazily
            via ``ensure_default_profile`` so the first request after the
            multi-profile rollout just works.
        """
        store = app.state.profile_store
        pid = request.headers.get("X-Profile-Id", "").strip()
        if pid:
            profile = store.get_profile(pid)
            if profile is None or profile.user_id != user_id or not profile.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {
                        "message": "Invalid or inaccessible profile.",
                        "type": "forbidden", "code": "invalid_profile",
                    }},
                )
            return pid
        # No header → default profile. Idempotent: safe to call on every request.
        default = store.ensure_default_profile(user_id=user_id)
        return default.id

    def _build_hermes_headers(user_id: str) -> dict[str, str]:
        """Build request headers for proxying to the user's hermes process.

        Model, toolset, and provider config are written directly into the
        profile's config.yaml via _sync_config_to_profile — no X-Hermes-*
        override headers needed, keeping the hermes gateway unmodified.

        Charter v2 §定律 4 — propagate the cloud user_id via
        X-Hermes-Cloud-User-Id so hermes can stamp agent_sessions
        correctly (patch 38 binds the header to SESSION_USER_ID
        ContextVar; agent_session_pg.create_session reads it).
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {cfg.internal_key}",
            "Content-Type": "application/json",
        }
        if user_id:
            headers["X-Hermes-Cloud-User-Id"] = str(user_id)
        return headers

    async def _proxy(
        request: Request,
        user_id: str,
        upstream_path: str,
        method: str | None = None,
        raw_body: bytes | None = None,
    ) -> Response:
        """Forward a request to the user's hermes process and return the response."""
        method = method or request.method
        profile_id = _resolve_profile_id(request, user_id)
        try:
            _sync_config_to_profile(user_id, profile_id)
        except Exception as exc:
            logger.warning(
                "failed to sync config before proxy uid=%s pid=%s: %s",
                user_id, profile_id, exc,
            )
        port = await app.state.profile_manager.ensure_running(
            profile_id, user_id=user_id
        )
        app.state.profile_manager.touch(profile_id)
        # Forward the original query string so filters like
        # ?include_disabled=true reach the hermes gateway.
        _query = request.url.query
        # B2 multiplex routing: prefix "/p/{profile}" when the mux gateway
        # serves this profile; "" (byte-identical to legacy) when flag off.
        _mux_prefix = app.state.profile_manager.mux_path_prefix(profile_id)
        url = f"http://127.0.0.1:{port}{_mux_prefix}{upstream_path}"
        if _query:
            url = f"{url}?{_query}"
        headers = _build_hermes_headers(user_id)
        if raw_body is None:
            raw_body = await request.body()
        # Forward conversation ID if present
        conv_id = request.headers.get("X-Conversation-Id", "")
        if conv_id:
            headers["X-Conversation-Id"] = conv_id
            headers["X-Hermes-Session-Id"] = conv_id
        resp = await app.state.http_client.request(
            method, url, headers=headers, content=raw_body
        )
        # Charter v2 Phase 3 (2026-05-06): RL live-capture removed —
        # chat round-trips are no longer logged as training signal.
        # Charter §一原语 ① — capture LLM usage so the /api/analytics
        # popup gets non-zero rows for chatbox traffic too. Hermes' own
        # /internal/agent/event tap doesn't carry output_tokens / model /
        # user_id today, so we ride the proxy response instead. Only
        # applies to /v1/chat/completions; ignore other upstream paths.
        if upstream_path == "/v1/chat/completions" and resp.status_code < 400:
            try:
                import json as _json2
                _resp_obj = _json2.loads(resp.content or b"{}")
                _resp_usage = _resp_obj.get("usage") or {}
                if _resp_usage:
                    from hermes_cloud.app.insights.llm_usage import record_llm_usage
                    _faux_msg = {
                        "__usage": _resp_usage,
                        "__model": _resp_obj.get("model") or "unknown",
                    }
                    record_llm_usage(
                        user_id=int(user_id),
                        purpose="chatbox",
                        msg=_faux_msg,
                        session_id=resp_conv_id or None,
                    )
            except Exception:
                logger.debug("chatbox_usage_record_failed", exc_info=True)
        out_headers = {}
        resp_conv_id = resp.headers.get("X-Conversation-Id", "")
        if resp_conv_id:
            out_headers["X-Conversation-Id"] = resp_conv_id
            # Charter v2 §定律 4 — session registration happens inside
            # the hermes subprocess via agent_session_pg.create_session,
            # which reads X-Hermes-Cloud-User-Id (patch 38) to set
            # user_id correctly. No parallel cloud-side session_store
            # write needed.
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
            headers=out_headers,
        )

    async def _proxy_stream(
        request: Request,
        user_id: str,
        upstream_path: str,
        raw_body: bytes,
    ) -> StreamingResponse:
        """Forward a streaming (SSE) request to the user's hermes process."""
        profile_id = _resolve_profile_id(request, user_id)
        try:
            _sync_config_to_profile(user_id, profile_id)
        except Exception as exc:
            logger.warning(
                "failed to sync config before stream proxy uid=%s pid=%s: %s",
                user_id, profile_id, exc,
            )
        port = await app.state.profile_manager.ensure_running(
            profile_id, user_id=user_id
        )
        app.state.profile_manager.touch(profile_id)
        # Forward the original query string so filters like
        # ?include_disabled=true reach the hermes gateway.
        _query = request.url.query
        # B2 multiplex routing: prefix "/p/{profile}" when the mux gateway
        # serves this profile; "" (byte-identical to legacy) when flag off.
        _mux_prefix = app.state.profile_manager.mux_path_prefix(profile_id)
        url = f"http://127.0.0.1:{port}{_mux_prefix}{upstream_path}"
        if _query:
            url = f"{url}?{_query}"
        headers = _build_hermes_headers(user_id)
        conv_id = request.headers.get("X-Conversation-Id", "")
        if conv_id:
            headers["X-Conversation-Id"] = conv_id
            headers["X-Hermes-Session-Id"] = conv_id

        # Inject user's system prompt (if set) as a system message in the request body.
        import json as _json  # noqa: PLC0415
        agents_cfg = app.state.config_store.get_user_agents_config(
            user_id, profile_id=profile_id
        )
        system_prompt = agents_cfg.system_prompt if agents_cfg else None
        if system_prompt:
            try:
                body_json = _json.loads(raw_body)
                msgs = body_json.get("messages", [])
                # Prepend system message only if one isn't already present
                if not any(m.get("role") == "system" for m in msgs):
                    body_json["messages"] = [{"role": "system", "content": system_prompt}] + msgs
                raw_body = _json.dumps(body_json).encode()
            except Exception:
                pass  # Malformed body — pass through unchanged

        # Charter 原语 ② — Memory Substrate recall. Pull top-k memories
        # relevant to the latest user turn and inject them as a system
        # message so every chat turn has long-term context. Best-effort;
        # failures (no pgvector, no memories yet, embedder offline) must
        # never break the stream.
        try:
            body_json = _json.loads(raw_body)
            msgs = body_json.get("messages", [])
            last_user = ""
            for m in reversed(msgs):
                if m.get("role") == "user":
                    c = m.get("content")
                    if isinstance(c, str):
                        last_user = c
                    elif isinstance(c, list):
                        last_user = " ".join(
                            str(p.get("text") or "")
                            for p in c
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    break
            if last_user.strip():
                from hermes.memory import search_memories
                hits = search_memories(
                    query=last_user[:2000],
                    user_id=user_id,
                    top_k=5,
                    min_similarity=0.25,
                )
                if hits:
                    lines = ["以下是与当前问题相关的历史记忆（按相关度排序，仅供参考）："]
                    for i, h in enumerate(hits, 1):
                        text = (h.get("content") or h.get("title") or "").strip()
                        if text:
                            lines.append(f"{i}. {text[:300]}")
                    recall_msg = {"role": "system", "content": "\n".join(lines)}
                    # Prepend — if another system msg already exists, place
                    # recall AFTER it so user's pinned system prompt wins.
                    existing = body_json.get("messages", [])
                    insert_at = 0
                    for idx, m in enumerate(existing):
                        if m.get("role") == "system":
                            insert_at = idx + 1
                        else:
                            break
                    body_json["messages"] = (
                        existing[:insert_at] + [recall_msg] + existing[insert_at:]
                    )
                    raw_body = _json.dumps(body_json).encode()
        except Exception as exc:
            logger.debug("memory_context_inject_failed uid=%s err=%r", user_id, exc)

        # Enter the upstream stream context first so we can read the response
        # headers (including X-Conversation-Id) before creating StreamingResponse.
        _timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
        upstream_cm = app.state.http_client.stream(
            "POST", url, headers=headers, content=raw_body, timeout=_timeout,
        )
        try:
            upstream = await upstream_cm.__aenter__()
        except Exception as exc:
            logger.error("stream_connect_error uid=%s err=%s", user_id, exc)
            raise

        final_conv_id = upstream.headers.get("X-Hermes-Session-Id", "") or upstream.headers.get("X-Conversation-Id", "") or conv_id or f"conv_{uuid.uuid4().hex[:16]}"

        # Session tracking (non-blocking)
        # Charter v2 §定律 4 — agent_session_pg.create_session writes
        # the row inside hermes; no cloud-side parallel registry.

        # Accumulate SSE chunks only for chat-completions; other streaming
        # paths are not a training-signal shape.
        _capture_chunks: list[bytes] | None = (
            [] if upstream_path == "/v1/chat/completions" else None
        )

        async def _stream_gen():
            # Charter L3 — accumulate assistant text as it streams by so
            # we can persist a single rl_agent_drafts row at the end.
            # This turns main-chat turns into supervision signal for the
            # Training Loop instead of copy-pasted advice.
            assistant_text_parts: list[str] = []
            sse_buf = ""
            # Charter §一原语 ① — capture usage from the trailing
            # ``include_usage`` chunk so chatbox traffic shows up in
            # /api/analytics. OpenAI stream protocol emits a final
            # ``data: {choices:[],usage:{...}}`` frame when the upstream
            # request had ``stream_options.include_usage=true``.
            captured_usage: Dict[str, Any] | None = None
            captured_model: str | None = None
            try:
                async for chunk in upstream.aiter_bytes():
                    if not chunk:
                        continue
                    # Also mirror raw chunks for §56 live-capture signal writer.
                    if _capture_chunks is not None:
                        _capture_chunks.append(chunk)
                    # Forward the byte as-is so streaming latency is
                    # unchanged — we only *observe* passing-by text.
                    yield chunk
                    try:
                        sse_buf += chunk.decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                    # Parse completed SSE frames (double newline delimited)
                    while "\n\n" in sse_buf:
                        frame, sse_buf = sse_buf.split("\n\n", 1)
                        for line in frame.splitlines():
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                obj = _json.loads(data_str)
                            except Exception:
                                continue
                            # Last frame: usage roll-up (and model echo).
                            u = obj.get("usage")
                            if isinstance(u, dict) and u:
                                captured_usage = u
                                if isinstance(obj.get("model"), str):
                                    captured_model = obj["model"]
                            delta = (
                                (obj.get("choices") or [{}])[0].get("delta") or {}
                            )
                            if delta.get("type") in ("reasoning", "thinking"):
                                continue  # reasoning is NOT the draft
                            c = delta.get("content")
                            if isinstance(c, str) and c:
                                assistant_text_parts.append(c)
            except Exception as exc:
                logger.error("stream_error uid=%s err=%s", user_id, exc)
                raise
            finally:
                await upstream_cm.__aexit__(None, None, None)
                if captured_usage:
                    try:
                        from hermes_cloud.app.insights.llm_usage import record_llm_usage
                        record_llm_usage(
                            user_id=int(user_id),
                            purpose="chatbox",
                            msg={
                                "__usage": captured_usage,
                                "__model": captured_model or "unknown",
                            },
                            session_id=final_conv_id or None,
                        )
                    except Exception:
                        logger.debug("chatbox_stream_usage_record_failed", exc_info=True)
                # Charter v2 Phase 3 (2026-05-06): RL drafts +
                # live-capture removed. Streaming response no longer
                # persists an rl_agent_drafts row or emits a trailing
                # ``hermes.draft.id`` SSE event; the SPA's FeedbackBar
                # was deleted along with the rest of the RL surface.

        return StreamingResponse(
            _stream_gen(),
            media_type="text/event-stream",
            headers={"X-Conversation-Id": final_conv_id},
        )

    # ------------------------------------------------------------------
    # SPA — the React app built from ../../frontend, served under /app/*.
    # The legacy static dashboard (hermes_cloud/static/) was removed.
    # ------------------------------------------------------------------
    _crm_spa_dir = _os.path.normpath(
        _os.path.join(_os.path.dirname(__file__), "..", "..", "frontend", "dist")
    )
    _crm_spa_index = _os.path.join(_crm_spa_dir, "index.html")
    if _os.path.isdir(_crm_spa_dir) and _os.path.exists(_crm_spa_index):
        from fastapi.staticfiles import StaticFiles
        # Serve /assets and the SPA under /app/*.
        app.mount(
            "/app/assets",
            StaticFiles(directory=_os.path.join(_crm_spa_dir, "assets")),
            name="crm_spa_assets",
        )

        @app.get("/app", include_in_schema=False)
        @app.get("/app/", include_in_schema=False)
        @app.get("/app/{path:path}", include_in_schema=False)
        async def crm_spa(request: Request, path: str = ""):  # noqa: ARG001
            # Real files at the dist root (favicon.svg, robots.txt, manifest,
            # …) must be served as-is. Without this, the SPA index fallback
            # below returns HTML for e.g. /app/favicon.svg, so the browser
            # tab icon silently breaks. (/app/assets/* is already handled by
            # the StaticFiles mount above and never reaches here.)
            if path:
                _candidate = _os.path.normpath(_os.path.join(_crm_spa_dir, path))
                if (
                    _candidate.startswith(_crm_spa_dir + _os.sep)
                    and _os.path.isfile(_candidate)
                ):
                    return FileResponse(_candidate)
            # SPA index.html references hashed asset bundles; if the
            # browser caches the *index* itself, a fresh build's new
            # hash never reaches the browser. The hashed assets under
            # /app/assets/* can stay cacheable forever, but index.html
            # must be revalidated every load.
            return FileResponse(
                _crm_spa_index,
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

        # HEAD /app/ — preview proxies (e.g. Claude Preview) use HEAD to
        # check server readiness before loading the page. FastAPI doesn't
        # auto-generate HEAD for routes, so we add it explicitly.
        @app.head("/app", include_in_schema=False)
        @app.head("/app/", include_in_schema=False)
        @app.head("/app/{path:path}", include_in_schema=False)
        async def crm_spa_head(request: Request, path: str = ""):  # noqa: ARG001
            return Response(status_code=200)

        # Unified SPA at /app is now the canonical UI. Root and /dashboard
        # redirect into it; legacy /dashboard/* paths are mapped to /app/*
        # since every legacy route (agents, approvals, analytics, admin,
        # login, register) has a SPA equivalent.
        _LEGACY_PATH_MAP = {
            "": "/app/",
            "/": "/app/",
            "agents": "/app/agents",
            "analytics": "/app/analytics",
            "admin/users": "/app/admin/users",
            "admin/processes": "/app/admin/processes",
            "approvals": "/app/approvals",
            "login": "/app/login",
            "register": "/app/register",
        }

        @app.get("/", include_in_schema=False)
        async def _root_redirect():  # noqa: D401
            return RedirectResponse(url="/app/", status_code=302)

        @app.get("/dashboard", include_in_schema=False)
        @app.get("/dashboard/", include_in_schema=False)
        @app.get("/dashboard/{path:path}", include_in_schema=False)
        async def _dashboard_redirect(path: str = ""):
            # /dashboard/agents/<id>[/<tab>] → /app/agents/<id>[/<tab>]
            mapped = _LEGACY_PATH_MAP.get(path)
            if mapped is not None:
                return RedirectResponse(url=mapped, status_code=302)
            # Pass through unknown sub-paths (e.g. agent detail routes).
            target = f"/app/{path}" if path else "/app/"
            return RedirectResponse(url=target, status_code=302)

        @app.get("/spa", include_in_schema=False)
        @app.get("/spa/{path:path}", include_in_schema=False)
        async def _spa_redirect(path: str = ""):
            target = f"/app/{path}" if path else "/app/"
            return RedirectResponse(url=target, status_code=302)

    else:
        # SPA bundle not built — ../../frontend/dist is missing. Broken
        # deploy; surface it clearly (the legacy static dashboard is gone).
        @app.get("/", include_in_schema=False)
        @app.get("/dashboard", include_in_schema=False)
        @app.get("/dashboard/{path:path}", include_in_schema=False)
        async def _spa_not_built(path: str = ""):  # noqa: ARG001
            return Response(
                "Frontend not built. Run: cd frontend && bun run build",
                status_code=503,
                media_type="text/plain",
            )

    # ------------------------------------------------------------------
    # Health + metrics
    # ------------------------------------------------------------------
    @app.get("/health", include_in_schema=False)
    def health_check():
        return {"status": "ok", "service": "hermes-cloud"}

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint():
        data = generate_latest(REGISTRY)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    # Charter v2 Phase 3 (2026-05-06): /admin/router_stats and
    # /admin/signal_writer_stats endpoints removed along with the RL
    # subsystem they reported on.

    # ------------------------------------------------------------------
    # Auth: register / login / logout
    # ------------------------------------------------------------------
    @app.get("/api/config/registration-status", include_in_schema=False)
    async def registration_status():
        return JSONResponse({"allow_registration": cfg.allow_registration})

    @app.post("/auth/register", status_code=201)
    async def auth_register(request: Request):
        if not cfg.allow_registration:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"message": "Self-registration is disabled.",
                                  "type": "forbidden", "code": "registration_disabled"}},
            )
        body = await _parse_json_body(request)
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", "")).strip()
        if not email or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "email and password are required.",
                                  "type": "invalid_request_error", "code": "missing_fields"}},
            )
        try:
            account = app.state.auth_store.register_user(email, password)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"message": str(exc), "type": "conflict_error", "code": "email_taken"}},
            )
        response = JSONResponse(
            status_code=201,
            content={"user_id": account.id, "api_key": account.api_key},
        )
        if cfg.session_cookie_secret:
            set_session_cookie(response, cfg.session_cookie_secret, account.id, account.api_key)
        return response

    @app.post("/auth/login")
    async def auth_login(request: Request):
        body = await _parse_json_body(request)
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", "")).strip()
        if not email or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "email and password are required.",
                                  "type": "invalid_request_error", "code": "missing_fields"}},
            )
        account = app.state.auth_store.authenticate_with_password(email, password)
        if account is None:
            logger.warning("password_auth_failed email=%s", email)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Invalid email or password.",
                                  "type": "authentication_error", "code": "invalid_credentials"}},
            )
        response = JSONResponse({
            "user_id": account.id,
            "api_key": account.api_key,
            "is_admin": account.is_admin,
        })
        if cfg.session_cookie_secret:
            set_session_cookie(response, cfg.session_cookie_secret, account.id, account.api_key)
        return response

    @app.post("/auth/logout")
    async def auth_logout(request: Request):
        response = JSONResponse({"ok": True})
        clear_session_cookie(response)
        return response

    # ------------------------------------------------------------------
    # Chat: proxy to per-user hermes process
    # ------------------------------------------------------------------
    @app.get("/v1/models")
    async def list_models(request: Request):
        user_id = require_bearer_auth(request, cfg)
        if not user_id:
            raise HTTPException(status_code=401, detail={"error": {"message": "Auth required."}})
        profile_id = _resolve_profile_id(request, user_id)
        port = await app.state.profile_manager.ensure_running(
            profile_id, user_id=user_id
        )
        try:
            _mux_prefix = app.state.profile_manager.mux_path_prefix(profile_id)
            resp = await app.state.http_client.get(
                f"http://127.0.0.1:{port}{_mux_prefix}/v1/models",
                headers={"Authorization": f"Bearer {cfg.internal_key}"},
            )
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        except Exception:
            return JSONResponse({"object": "list", "data": [{"id": "hermes", "object": "model"}]})

    @app.post("/api/chat/attachments")
    async def chat_attachments(request: Request):
        """处理主对话的附件上传:图片→base64 data_url(喂视觉模型),
        PDF/Excel/Word/CSV→服务端抽文本。返回归一化附件,前端据此组多模态
        消息发给 agent(/v1/chat/completions)。agent 据「录单」技能抽字段录记录。"""
        user_id = require_bearer_auth(request, cfg)
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Authentication required.",
                                  "type": "authentication_error", "code": "unauthorized"}},
            )
        form = await request.form()
        raw_files: list = []
        for up in form.getlist("files"):
            if hasattr(up, "read"):  # starlette UploadFile, not a plain str
                data = await up.read()
                raw_files.append((
                    getattr(up, "filename", None) or "file",
                    data,
                    getattr(up, "content_type", None) or "",
                ))
        from hermes_cloud.app.llm.attachments import prepare_attachments
        attachments = prepare_attachments(raw_files) if raw_files else []

        # Persist the originals keyed by conversation so the agent's record
        # writes in this same chat can be associated back to these files
        # (emit_business_write links them). Best-effort — never blocks chat.
        conv_id = request.headers.get("X-Conversation-Id", "").strip()
        if raw_files and conv_id:
            try:
                profile_id = _resolve_profile_id(request, user_id)
                from hermes_cloud.app.attachments.repo import persist_uploads
                from hermes.crm.db import db_session
                with db_session() as db:
                    ids = persist_uploads(
                        db, raw_files=raw_files, owner_user_id=int(user_id),
                        profile_id=profile_id, conversation_id=conv_id,
                    )
                for att, aid in zip(attachments, ids):
                    att["id"] = aid
            except Exception:
                logger.exception("persist_uploads_failed")
        return {"attachments": attachments}

    @app.get("/api/attachments/{attachment_id}")
    async def download_attachment(attachment_id: int, request: Request):
        """Stream a persisted upload original. Owner-only. Images and PDFs
        render inline (preview); everything else downloads."""
        user_id = require_bearer_auth(request, cfg)
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Authentication required.",
                                  "type": "authentication_error", "code": "unauthorized"}},
            )
        from hermes.crm.db import db_session
        from hermes_cloud.app.attachments.repo import get_attachment
        from hermes_cloud.app.attachments.storage import LocalDiskStore
        with db_session() as db:
            att = get_attachment(db, attachment_id=int(attachment_id))
        if not att:
            raise HTTPException(status_code=404, detail="attachment not found")
        if str(att["owner_user_id"]) != str(user_id):
            raise HTTPException(status_code=403, detail="forbidden")
        store = LocalDiskStore()
        if not store.exists(att["stored_path"]):
            raise HTTPException(status_code=410, detail="blob missing")
        mime = att["mime_type"] or "application/octet-stream"
        inline = att.get("kind") == "image" or mime == "application/pdf"
        disp = "inline" if inline else "attachment"
        from urllib.parse import quote
        fn = quote(att["filename"] or "file")
        return StreamingResponse(
            store.open(att["stored_path"]),
            media_type=mime,
            headers={"Content-Disposition": f"{disp}; filename*=UTF-8''{fn}"},
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        user_id = require_bearer_auth(request, cfg)
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Authentication required.",
                                  "type": "authentication_error", "code": "unauthorized"}},
            )
        raw_body = await request.body()
        # Detect streaming from raw body to avoid double-parsing
        stream = b'"stream":true' in raw_body or b'"stream": true' in raw_body
        logger.info("chat uid=%s stream=%s", user_id, stream)

        # Phase 4.3 — gateway text-command short-circuit (撤销 / 强制撤销 /
        # 通过 / 拒绝 / 改). Runs BEFORE @specialist so a typed revert
        # command never accidentally matches a specialist prefix.
        try:
            gw_short = _maybe_gateway_command_short_circuit(
                raw_body=raw_body,
                user_id=user_id,
                profile_id=_resolve_profile_id(request, user_id),
                stream=stream,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("gateway_command_short_circuit_unexpected err=%r", exc)
            gw_short = None
        if gw_short is not None:
            return gw_short

        # Phase 3.1 — @specialist prefix short-circuit. If the latest user
        # message starts with `@<specialist> ...` for one of the 6 known
        # specialists, route to invoke() instead of the per-profile hermes
        # subprocess. Unknown prefixes / no prefix fall through unchanged.
        #
        # v3.3.1 feature flag — when ``cloud_orchestrate_specialists`` is
        # False (default), this short-circuit is skipped so the request
        # falls through to the per-profile hermes subprocess proxy and the
        # main agent inside hermes gets first refusal.
        if not getattr(cfg, "cloud_orchestrate_specialists", False):
            global _V331_DASHBOARD_LOG_ONCE
            if not _V331_DASHBOARD_LOG_ONCE:
                logger.info(
                    "v3.3.1 flag gate active, cloud orchestration disabled, "
                    "deferring to hermes main agent (path=dashboard.chat)"
                )
                _V331_DASHBOARD_LOG_ONCE = True
            short = None
        else:
            try:
                short = _maybe_specialist_short_circuit(
                    raw_body=raw_body,
                    user_id=user_id,
                    profile_id=_resolve_profile_id(request, user_id),
                    stream=stream,
                )
            except HTTPException:
                raise
            except Exception as exc:  # never fail the chat path on prefix bugs
                logger.warning("specialist_prefix_unexpected_error err=%r", exc)
                short = None
        if short is not None:
            return short

        # ③b /learn — 「把刚才这套流程存成技能」。若最新 user turn 是一次
        # ``/learn`` 触发(SPA「存为技能」按钮发送 ``/learn [聚焦描述]``,或
        # 用户在输入框直接打),把这一 turn 改写成遵循 Hermes 技能撰写规范的
        # standards-guided 指令,再原样走 proxy 流式代理。改写只动这一 turn 的
        # 文本,不动 model/stream/其余消息,因此 agent 在**同一 session**看到
        # 之前的完整对话历史,能提炼「刚才这套流程」并用 ``skill_manage`` 落盘
        # 到该 profile 的技能库。IM 侧 ``/learn`` 走 gateway slash 分发;SPA 走
        # 这条 ``/v1/chat/completions`` 代理,不经 gateway slash 层,故在此改写。
        try:
            from hermes_cloud.app.learn import maybe_rewrite_learn_body
            _learn_body = maybe_rewrite_learn_body(raw_body)
            if _learn_body is not None:
                logger.info("learn_rewrite uid=%s", user_id)
                raw_body = _learn_body
        except Exception as exc:  # 绝不因 learn 改写失败而挂掉普通聊天
            logger.warning("learn_rewrite_unexpected err=%r", exc)

        try:
            if stream:
                return await _proxy_stream(request, user_id, "/v1/chat/completions", raw_body)
            else:
                return await _proxy(request, user_id, "/v1/chat/completions",
                                    method="POST", raw_body=raw_body)
        except RuntimeError as exc:
            logger.error("proxy_error uid=%s err=%s", user_id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"message": str(exc), "type": "server_error", "code": "process_unavailable"}},
            )

    # ------------------------------------------------------------------
    # User config: model / toolsets / prompt
    # ------------------------------------------------------------------
    @app.get("/api/user-config/model")
    def get_user_model_config(request: Request):
        import json as _json
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        stored = app.state.config_store.get_user_model(user_id, profile_id=profile_id)
        providers_json = stored.providers_json if stored else None
        derived = None
        if providers_json:
            try:
                pj = _json.loads(providers_json) if isinstance(providers_json, str) else providers_json
                derived = {
                    "auxiliary": model_roles.resolve_auxiliary(pj),
                    "moa": model_roles.resolve_moa(pj),
                }
            except Exception:
                derived = None
        return {
            "model": stored.model if stored else None,
            "api_key": stored.api_key if stored else None,
            "base_url": stored.base_url if stored else None,
            "providers_json": providers_json,
            "derived": derived,
        }

    @app.put("/api/user-config/model")
    async def put_user_model_config(request: Request):
        import json as _json
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        body = await _parse_json_body(request)
        providers_json_raw = body.get("providers_json")
        providers_json_str = (
            _json.dumps(providers_json_raw) if isinstance(providers_json_raw, dict)
            else (providers_json_raw if isinstance(providers_json_raw, str) else None)
        )
        derived_model = body.get("model")
        derived_api_key = body.get("api_key")
        derived_base_url = body.get("base_url")
        if providers_json_str:
            try:
                pcfg = _json.loads(providers_json_str) if isinstance(providers_json_str, str) else providers_json_raw
                default_model_ref = pcfg.get("default_model", "")
                providers = pcfg.get("providers", {})
                if "::" in str(default_model_ref):
                    prov_id, model_id = default_model_ref.split("::", 1)
                    prov = providers.get(prov_id, {})
                    derived_model = model_id or derived_model
                    derived_api_key = prov.get("api_key") or derived_api_key
                    derived_base_url = prov.get("base_url") or derived_base_url
            except Exception:
                pass
        profile_id = _resolve_profile_id(request, user_id)
        # Capture pre-save api_key / base_url so we only kick the gateway when
        # the env-affecting fields actually changed. The model name (slug) is
        # read per-request from config.yaml — switching models needs no
        # restart, only key/provider/base_url changes do.
        try:
            _prev = app.state.config_store.get_user_model(user_id, profile_id=profile_id)
            _prev_key = (_prev.api_key if _prev else None) or ""
            _prev_base = (_prev.base_url if _prev else None) or ""
        except Exception:
            _prev_key = ""; _prev_base = ""
        stored = app.state.config_store.set_user_model(
            user_id=user_id,
            profile_id=profile_id,
            model=derived_model,
            api_key=derived_api_key,
            base_url=derived_base_url,
            providers_json=providers_json_str,
        )
        # Sync model config into hermes profile config.yaml so the gateway
        # process can resolve inference credentials without headers.
        try:
            _sync_config_to_profile(user_id, profile_id)
        except Exception as exc:
            logger.warning("failed to sync model config to profile uid=%s: %s", user_id, exc)
        # Kick the gateway ONLY when env-affecting fields changed. hermes
        # resolves provider API keys (OPENCODE_ZEN_API_KEY etc.) from env
        # at spawn time (profile_manager.py:783-799), so an in-place
        # config.yaml edit doesn't reach a running gateway. But restarting
        # on every PUT is too aggressive — SPA debounce-saves on each
        # keystroke / picker change, which used to thrash the gateway and
        # land messages in the "restart in flight" window. Only the api_key
        # and base_url affect the spawn env; the model slug is read per-
        # request from config.yaml and needs no restart.
        _new_key = (stored.api_key or "")
        _new_base = (stored.base_url or "")
        _env_changed = (_new_key != _prev_key) or (_new_base != _prev_base)
        if _env_changed:
            try:
                await app.state.profile_manager.restart_profile(
                    profile_id, user_id=str(user_id),
                )
                logger.info(
                    "model config: env changed (key/base_url) → gateway "
                    "restarted uid=%s profile=%s",
                    user_id, profile_id,
                )
            except Exception as exc:
                logger.warning(
                    "model config saved but gateway restart failed "
                    "(new key won't take effect until next restart) "
                    "uid=%s profile=%s: %s",
                    user_id, profile_id, exc,
                )
        else:
            logger.debug(
                "model config saved without gateway restart "
                "(api_key/base_url unchanged) uid=%s profile=%s",
                user_id, profile_id,
            )
        # Return the freshly-derived model roles so the SPA's "当前生效"
        # labels + MoA auto-highlight update immediately after save without
        # a manual reload (GET computes the same block).
        derived = None
        if stored.providers_json:
            try:
                _pj = (
                    _json.loads(stored.providers_json)
                    if isinstance(stored.providers_json, str)
                    else stored.providers_json
                )
                derived = {
                    "auxiliary": model_roles.resolve_auxiliary(_pj),
                    "moa": model_roles.resolve_moa(_pj),
                }
            except Exception:
                derived = None
        return {"model": stored.model, "api_key": stored.api_key,
                "base_url": stored.base_url, "providers_json": stored.providers_json,
                "derived": derived}

    @app.get("/api/me/active-models")
    def get_active_models(request: Request):
        """Return the user's profile-active LLM models for the chat-box
        ModelPicker dropdown. Server-side parsing of ``providers_json`` so
        the SPA doesn't reimplement the schema.

        Response shape::

            {
              "models": [
                {"id": "openai::gpt-4o-mini", "label": "gpt-4o-mini",
                 "provider_id": "openai"},
                ...
              ],
              "default_id": "openai::gpt-4o-mini",
              "configured": true
            }

        When the user has not configured any model (no providers_json
        or empty providers), returns ``configured: false`` with empty
        list — front-end shows a "go configure" hint and disables send.
        """
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        from hermes_cloud.app.llm_creds import active_models_for_user
        return active_models_for_user(
            app.state.config_store, user_id, profile_id=profile_id,
            # ②b:传 profile 目录,让 active_models 额外读 config.yaml 的
            # moa.presets,把「深度会诊」档追加进 ModelPicker。
            profile_dir=_profile_dir(profile_id or user_id),
        )

    @app.get("/api/user-config/toolsets")
    def get_user_toolsets_config(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        stored = app.state.config_store.get_user_toolsets(user_id, profile_id=profile_id)
        return {"toolsets": stored.toolsets if stored else None}

    @app.put("/api/user-config/toolsets")
    async def put_user_toolsets_config(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        body = await _parse_json_body(request)
        toolsets = body.get("toolsets")
        if not isinstance(toolsets, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "Missing or invalid 'toolsets' field.",
                                  "type": "invalid_request_error", "code": "invalid_toolsets"}},
            )
        stored = app.state.config_store.set_user_toolsets(
            user_id=user_id, profile_id=profile_id,
            toolsets=[str(i) for i in toolsets]
        )
        try:
            _sync_config_to_profile(user_id, profile_id)
        except Exception as exc:
            logger.warning("failed to sync toolsets to profile uid=%s: %s", user_id, exc)
        return {"toolsets": stored.toolsets}

    # /api/user-config/prompt + user_prompt_configs table removed
    # 2026-05-18: dead code. No SPA caller, no backend reader other
    # than the endpoint itself, 0 rows in PG. SOUL.md / personality
    # endpoints cover the per-profile prompt-identity use case.

    import pathlib as _pathlib
    import yaml as _yaml

    def _profile_skills_layout(profile_id: str) -> tuple[_Path, _Path]:
        """Return ``(skills_dir, config_yaml)`` for a profile.

        Per-profile skill on/off is a hermes-native feature: each profile's
        gateway runs with ``HERMES_HOME=<profile_dir>``, so it discovers
        skills from ``<profile_dir>/skills/`` and reads the disabled list
        from ``<profile_dir>/config.yaml`` (``skills.disabled``). We read
        and write those files directly so cloud-side toggles take effect
        at chat time instead of writing a dead cloud-only table.
        """
        profile_dir = app.state.profile_manager._hermes_home / "profiles" / profile_id
        return profile_dir / "skills", profile_dir / "config.yaml"

    def _scan_profile_skills(skills_dir: _Path) -> list[dict]:
        """Walk ``skills_dir`` for SKILL.md files and return metadata."""
        if not skills_dir.is_dir():
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            rel = skill_md.parent.relative_to(skills_dir)
            default_name = str(rel).replace(_os.sep, "/")
            name = default_name
            desc = ""
            try:
                text = skill_md.read_text(encoding="utf-8")[:4000]
                # Minimal YAML frontmatter parse — name + description only.
                if text.startswith("---"):
                    end = text.find("\n---", 3)
                    if end > 0:
                        for line in text[3:end].splitlines():
                            line = line.strip()
                            if line.startswith("name:"):
                                v = line.split(":", 1)[1].strip().strip('"').strip("'")
                                if v:
                                    name = v[:64]
                            elif line.startswith("description:"):
                                desc = line.split(":", 1)[1].strip().strip('"').strip("'")[:1024]
            except Exception:
                pass
            if name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "description": desc})
        return out

    def _read_profile_disabled(config_yaml: _Path) -> set[str]:
        if not config_yaml.is_file():
            return set()
        try:
            data = _yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            return set()
        skills_cfg = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(skills_cfg, dict):
            return set()
        disabled = skills_cfg.get("disabled")
        if not isinstance(disabled, list):
            return set()
        return {str(x) for x in disabled}

    def _write_profile_disabled(config_yaml: _Path, disabled: set[str]) -> None:
        config_yaml.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            data = {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        skills_cfg = data.get("skills")
        if not isinstance(skills_cfg, dict):
            skills_cfg = {}
            data["skills"] = skills_cfg
        skills_cfg["disabled"] = sorted(disabled)
        config_yaml.write_text(_yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    @app.get("/api/user-config/skills")
    async def get_user_skills(request: Request):
        """Return skills visible to this user, split into two origin groups.

        * ``private`` — ``~/.hermes/profiles/<pid>/skills/`` — agent-learned,
          user-owned.
        * ``global`` — ``~/.hermes/skills/`` — org-shared.

        Both groups are fully toggleable by the owning user. (Per-role
        gating of skills was removed; see migration 0085.)

        ``skills`` / ``enabled`` are kept for backward compat with older
        frontend builds — flat list of private + global.
        """
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        skills_dir, config_yaml = _profile_skills_layout(profile_id)
        private_raw = _scan_profile_skills(skills_dir)
        disabled = _read_profile_disabled(config_yaml)

        # Global skills live next to profiles/ at ~/.hermes/skills/.
        global_root = app.state.profile_manager._hermes_home / "skills"
        global_raw = _scan_profile_skills(global_root)

        private = [
            {"name": s["name"], "description": s["description"],
             "origin": "private",
             "enabled": s["name"] not in disabled,
             "locked": is_default_crm_skill(s["name"])}
            for s in private_raw
        ]
        # Subtract private names from global to avoid double-listing if a
        # skill exists in both trees (private shadows global).
        private_names = {s["name"] for s in private_raw}
        global_list = [
            {"name": s["name"], "description": s["description"],
             "origin": "global",
             "enabled": s["name"] not in disabled,
             "locked": is_default_crm_skill(s["name"])}
            for s in global_raw if s["name"] not in private_names
        ]

        # Legacy flat shape.
        skills_flat = private + global_list
        enabled = [s["name"] for s in skills_flat if s["enabled"]]
        return JSONResponse({
            "private": private,
            "global": global_list,
            "skills": skills_flat,
            "enabled": enabled,
        })

    @app.post("/api/user-config/skills")
    async def set_user_skills(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        body = await _parse_json_body(request)
        desired_enabled = {str(s).strip() for s in body.get("skills", []) if str(s).strip()}
        skills_dir, config_yaml = _profile_skills_layout(profile_id)
        available = _scan_profile_skills(skills_dir)
        global_root = app.state.profile_manager._hermes_home / "skills"
        global_available = _scan_profile_skills(global_root)
        private_names = {s["name"] for s in available}
        global_names = {g["name"] for g in global_available}
        # Universe the user may toggle: private ∪ global.
        togglable = private_names | (global_names - private_names)
        # 系统默认 CRM 技能只增不减:crm-* 永不进 disabled(见 skill_policy)。
        new_disabled = resolve_disabled(togglable, desired_enabled)
        _write_profile_disabled(config_yaml, new_disabled)
        return JSONResponse({"skills": sorted(togglable - new_disabled),
                             "disabled": sorted(new_disabled)})

    @app.get("/api/user-config/agents")
    def get_user_agents_config(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        stored = app.state.config_store.get_user_agents_config(
            user_id, profile_id=profile_id
        )
        return {
            "system_prompt": stored.system_prompt if stored else None,
            "memory_enabled": stored.memory_enabled if stored else True,
        }

    @app.post("/api/user-config/agents")
    async def set_user_agents_config(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        body = await _parse_json_body(request)
        stored = app.state.config_store.set_user_agents_config(
            user_id=user_id,
            profile_id=profile_id,
            system_prompt=body.get("system_prompt"),
            memory_enabled=bool(body.get("memory_enabled", True)),
        )
        return {"system_prompt": stored.system_prompt, "memory_enabled": stored.memory_enabled}

    # ------------------------------------------------------------------
    # Profile (Agent) CRUD
    # ------------------------------------------------------------------

    def _profile_to_dict(p) -> dict:
        return {
            "id": p.id,
            "user_id": p.user_id,
            "name": p.name,
            "is_default": p.is_default,
            "is_active": p.is_active,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "description": getattr(p, "description", "") or "",
            "slug": getattr(p, "slug", "") or "",
            # Deployment-level topology flag (always present, independent of
            # whether a process is currently up). Under multiplex a SINGLE
            # shared gateway serves every profile, so per-profile start/stop
            # controls are meaningless (stopping it drops all tenants) — the
            # SPA hides those buttons when this is true. Kept at the profile
            # level (not on ``process``) so the UI can decide even when the
            # mux is momentarily down and ``process`` is None.
            "multiplex": bool(app.state.profile_manager._multiplex_enabled()),
        }

    @app.get("/api/profiles")
    def list_profiles_endpoint(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        # 保证默认 profile 存在（legacy 用户第一次调用时懒建）
        app.state.profile_store.ensure_default_profile(user_id=user_id)
        profiles = app.state.profile_store.list_profiles(user_id=user_id)
        # Attach per-profile process info so the user-facing 个人信息
        # modal can show "Agent 运行中 / 端口 / PID" for the user's own
        # profiles, mirroring what admins see in the Users page detail
        # drawer.
        proc_by_profile = {
            str(p["profile_id"]): p
            for p in app.state.profile_manager.get_running_processes()
        }
        out = []
        for p in profiles:
            d = _profile_to_dict(p)
            proc = proc_by_profile.get(str(p.id))
            d["process"] = {
                "running": proc is not None,
                "port": proc.get("port") if proc else None,
                "pid": proc.get("pid") if proc else None,
                "idle_seconds": proc.get("idle_seconds") if proc else None,
                # multiplex: the profile is served by the shared mux gateway
                # (same port/pid across profiles). UI can label it as shared.
                "multiplexed": bool(proc.get("multiplexed")) if proc else False,
            }
            out.append(d)
        return {"profiles": out}

    @app.post("/api/profiles/{profile_id}/start")
    async def self_start_profile_process(profile_id: str, request: Request):
        """Start the caller's own profile subprocess (self-scoped)."""
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile = app.state.profile_store.get_profile(profile_id)
        if profile is None or profile.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"message": "Forbidden",
                                  "type": "forbidden",
                                  "code": "not_owner"}},
            )
        try:
            port = await app.state.profile_manager.ensure_running(
                profile_id, user_id=profile.user_id,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"message": str(exc),
                                  "type": "server_error",
                                  "code": "process_unavailable"}},
            )
        return {"ok": True, "port": port}

    @app.delete("/api/profiles/{profile_id}/process")
    async def self_stop_profile_process(profile_id: str, request: Request):
        """Stop the caller's own profile subprocess (self-scoped)."""
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile = app.state.profile_store.get_profile(profile_id)
        if profile is None or profile.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"message": "Forbidden",
                                  "type": "forbidden",
                                  "code": "not_owner"}},
            )
        await app.state.profile_manager.stop_profile(profile_id)
        return {"ok": True}

    @app.post("/api/profiles", status_code=201)
    async def create_profile_endpoint(request: Request):
        # Charter v2 §简化原则: 1 user = 1 profile. profile_id is always
        # equal to user_id. Multi-profile-per-user is removed; this endpoint
        # is kept as a 410 Gone so any UI / API client calling it surfaces
        # a clear deprecation message instead of silently building a hex
        # profile that will never be routable.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "error": {
                    "message": (
                        "Multi-profile is removed (Charter v2). Each user has "
                        "exactly one profile, with profile_id == user_id. "
                        "Use the default profile that was created at signup."
                    ),
                    "type": "feature_removed",
                    "code": "multi_profile_deprecated",
                }
            },
        )

    @app.patch("/api/profiles/{profile_id}")
    async def patch_profile_endpoint(profile_id: str, request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        store = app.state.profile_store
        # 存在性 + 归属权校验：非本人 → 404（不泄露存在性）
        profile = store.get_profile(profile_id)
        if profile is None or profile.user_id != user_id or not profile.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "Profile not found.",
                                  "type": "not_found_error", "code": "profile_not_found"}},
            )
        body = await _parse_json_body(request)
        changed: "Profile | None" = profile
        if "name" in body:
            try:
                changed = store.rename_profile(
                    profile_id, user_id=user_id, new_name=str(body["name"])
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"message": str(exc),
                                      "type": "invalid_request_error",
                                      "code": "invalid_profile_name"}},
                )
            if changed is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"message": "Profile not found.",
                                      "type": "not_found_error",
                                      "code": "profile_not_found"}},
                )
        if "description" in body or "slug" in body:
            try:
                changed = store.update_profile(
                    profile_id,
                    user_id=user_id,
                    description=body.get("description") if "description" in body else None,
                    slug=body.get("slug") if "slug" in body else None,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"message": str(exc),
                                      "type": "invalid_request_error",
                                      "code": "invalid_profile_field"}},
                )
            if changed is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"message": "Profile not found.",
                                      "type": "not_found_error",
                                      "code": "profile_not_found"}},
                )
        if body.get("is_default") is True:
            store.set_default(user_id=user_id, profile_id=profile_id)
            changed = store.get_profile(profile_id)
        return _profile_to_dict(changed)

    @app.delete("/api/profiles/{profile_id}", status_code=204)
    async def delete_profile_endpoint(profile_id: str, request: Request):
        from hermes_cloud.app.profile_store import CannotDeleteDefaultProfileError

        user_id = require_bearer_auth(request, cfg) or "local-dev"
        store = app.state.profile_store
        profile = store.get_profile(profile_id)
        if profile is None or profile.user_id != user_id or not profile.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "Profile not found.",
                                  "type": "not_found_error", "code": "profile_not_found"}},
            )
        try:
            store.delete_profile(profile_id, user_id=user_id)
        except CannotDeleteDefaultProfileError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"message": str(exc),
                                  "type": "conflict",
                                  "code": "cannot_delete_default_profile"}},
            )
        return Response(status_code=204)

    @app.get("/api/sessions")
    def list_sessions(request: Request):
        """Charter v2 §定律 4 — single source of truth: agent_sessions.

        Every conversational entry point (chatbox / copilot / decisions
        / dashboards / TUI / 飞书 / 微信 / cron) flows through the
        per-profile hermes subprocess and writes here. With hermes
        patch 38 + cloud's binding lookup in agent_session_pg, the
        user_id column is correctly populated for all surfaces; the
        inbox query is now one SELECT, no cloud-side parallel registry.
        """
        user_id = require_bearer_auth(request, cfg)
        include_hidden = (
            (request.query_params.get("include_hidden") or "").strip()
            in ("1", "true", "yes")
        )
        try:
            uid_int = int(user_id) if str(user_id).isdigit() else None
        except Exception:
            uid_int = None
        if uid_int is None:
            return JSONResponse({"data": []})

        whitelist = list(_INBOX_SOURCE_WHITELIST)
        # B2: every IM-originated agent_session is stamped with the
        # canonical cloud user_id at creation (see agent_session_pg.py),
        # so a plain user_id match is sufficient — no binding lookup.

        from hermes.crm.db import db_session as _db_session
        try:
            with _db_session() as db:
                rows = db.fetchall(
                    """
                    SELECT
                        asn.id                   AS conversation_id,
                        asn.source               AS source,
                        asn.user_id              AS raw_user_id,
                        to_timestamp(asn.started_at) AS started_at,
                        -- Last ACTIVITY, not creation/end bookkeeping:
                        -- IM sessions are chat-keyed and live for months
                        -- (a June feishu DM session gets today's turns
                        -- appended). COALESCE(ended_at, started_at) showed
                        -- such a session as months old and the started_at
                        -- sort buried it — 张可/wangz 2026-08-16 实测:飞书
                        -- 当天对话在 SPA 收件箱里完全找不到。
                        to_timestamp(COALESCE(last_msg.ts, asn.ended_at,
                                              asn.started_at))
                                                 AS last_active_at,
                        asn.message_count        AS message_count,
                        asn.chat_name            AS chat_name,
                        asn.thread_id            AS thread_id,
                        first_user_msg.content   AS first_user_content,
                        last_msg.content         AS last_content
                      FROM agent_sessions asn
                      LEFT JOIN LATERAL (
                          SELECT content FROM agent_messages
                           WHERE session_id = asn.id AND role = 'user'
                             -- Timeline bookkeeping rows (display_kind set:
                             -- hidden compaction carriers, model_switch, …)
                             -- are durable role='user' rows but no client
                             -- counts them as user turns — keep them out of
                             -- the derived title (mirrors upstream
                             -- list_recent_user_messages).
                             AND (display_kind IS NULL OR display_kind = '')
                           ORDER BY timestamp ASC LIMIT 1
                      ) first_user_msg ON true
                      LEFT JOIN LATERAL (
                          SELECT content, timestamp AS ts FROM agent_messages
                           WHERE session_id = asn.id
                             AND role IN ('user','assistant')
                             AND content IS NOT NULL AND content <> ''
                             AND (display_kind IS NULL OR display_kind = '')
                           ORDER BY timestamp DESC LIMIT 1
                      ) last_msg ON true
                     WHERE asn.user_id = %s
                       AND asn.source = ANY(%s)
                       -- Charter v2 §定律 4 — utility/cron sessions
                       -- (run_utility_skill prefix, daily_brief jobs,
                       -- generators) are system-driven, not user
                       -- conversations. Keep them out of the inbox.
                       AND asn.id NOT LIKE 'utility-%%'
                       AND asn.id NOT LIKE 'daily_brief-%%'
                       AND asn.id NOT LIKE 'record_question-%%'
                       AND asn.id NOT LIKE 'brief_generator-%%'
                       AND asn.id NOT LIKE 'question_generator-%%'
                     ORDER BY COALESCE(last_msg.ts, asn.ended_at,
                                       asn.started_at) DESC
                     LIMIT 200
                    """,
                    (str(uid_int), whitelist),
                ) or []
        except Exception as exc:
            logger.warning(
                "list_sessions failed user=%s err=%r", user_id, exc,
            )
            return JSONResponse({"data": []})

        data = []
        for r in rows:
            sid = str(r["conversation_id"])
            source = _normalize_session_source(str(r["source"] or ""))
            la = r["last_active_at"]
            sa = r["started_at"]
            # Title: prefer the explicit chat_name (set by the user
            # via PATCH or by IM platform metadata), else derive from
            # the first user message so wechat/feishu sessions show
            # the contact's opening line instead of "New conversation".
            title = (r.get("chat_name") or "").strip()
            if not title:
                first_msg = (r.get("first_user_content") or "").strip()
                if first_msg:
                    title = first_msg[:60]
            if not title:
                title = _DEFAULT_TITLE
            # Preview: the latest non-empty message text.
            preview = (r.get("last_content") or "").strip()[:200]
            data.append({
                "id": sid,
                "title": title,
                "preview": preview,
                "updated_at": la.isoformat() if la else (sa.isoformat() if sa else None),
                "source": source,
                "platform": source,
                "message_count": int(r.get("message_count") or 0),
                "hidden_at": None,  # archival not supported on agent_sessions yet
            })
        return JSONResponse({"data": data})

    @app.patch("/api/sessions/{conv_id}", include_in_schema=False)
    async def patch_session(conv_id: str, request: Request):
        """Rename a session's chat title. Operates on agent_sessions
        directly (the single source of truth in charter v2 §定律 4)."""
        user_id = require_bearer_auth(request, cfg)
        body = await request.json()
        new_title = (body.get("title") or "").strip()
        if not new_title:
            return JSONResponse({"ok": True})
        try:
            uid_int = int(user_id) if str(user_id).isdigit() else None
        except Exception:
            uid_int = None
        if uid_int is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            from hermes.crm.db import db_session as _db_session
            with _db_session() as db:
                row = db.fetchone(
                    "SELECT user_id FROM agent_sessions WHERE id = %s",
                    (conv_id,),
                )
                if row is None or str(row.get("user_id")) != str(uid_int):
                    return JSONResponse({"error": "not found"}, status_code=404)
                db.execute(
                    "UPDATE agent_sessions SET chat_name = %s WHERE id = %s",
                    (new_title[:255], conv_id),
                )
        except Exception as exc:
            logger.warning("patch_session failed sid=%s err=%r", conv_id, exc)
            return JSONResponse({"error": "patch failed"}, status_code=500)
        return JSONResponse({"ok": True})

    @app.delete("/api/sessions/{conv_id}", include_in_schema=False)
    def delete_session(conv_id: str, request: Request):
        """Hard-delete a session. Cascades to agent_messages."""
        user_id = require_bearer_auth(request, cfg)
        try:
            uid_int = int(user_id) if str(user_id).isdigit() else None
        except Exception:
            uid_int = None
        if uid_int is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            from hermes.crm.db import db_session as _db_session
            with _db_session() as db:
                row = db.fetchone(
                    "SELECT user_id FROM agent_sessions WHERE id = %s",
                    (conv_id,),
                )
                if row is None or str(row.get("user_id")) != str(uid_int):
                    return JSONResponse({"error": "not found"}, status_code=404)
                db.execute(
                    "DELETE FROM agent_messages WHERE session_id = %s",
                    (conv_id,),
                )
                db.execute(
                    "DELETE FROM agent_sessions WHERE id = %s",
                    (conv_id,),
                )
        except Exception as exc:
            logger.warning("delete_session failed sid=%s err=%r", conv_id, exc)
            return JSONResponse({"error": "delete failed"}, status_code=500)
        return JSONResponse({"ok": True})

    @app.get("/api/sessions/{conv_id}/messages", include_in_schema=False)
    async def get_session_messages(conv_id: str, request: Request):
        """Return all messages for a session.

        Tries three sources in order — historical sessions ended up in
        different stores depending on when they were created:

        1. **PG ``agent_messages``** — sessions touched after PgSessionDB
           was wired (commit 98694bc, 2026-05-03+). Source of truth for
           anything new.
        2. **hermes profile ``state.db``** (sqlite) — sessions handled by
           the per-profile gateway between v0.6 and 98694bc. ``messages``
           table mirrors agent_messages columns.
        3. **JSON session file** ``~/.hermes/profiles/<pid>/sessions/
           session_<conv_id>.json`` — earliest archive shape for
           web-mo*/api-* sessions. Bare list of dicts under "messages"
           or "history".

        Each fallback returns identical ``{role, content, timestamp,
        tool_calls?, tool_call_id?, tool_name?, reasoning?,
        reasoning_details?, finish_reason?}`` shape so SPA renders
        the same regardless of source.
        """
        user_id = require_bearer_auth(request, cfg)
        # Charter v2 §简化原则: profile_id == user_id (slug, not numeric).
        # Fallback to user_id when X-Profile-Id header is absent.
        profile_id = (request.headers.get("X-Profile-Id") or "").strip() or str(user_id or "")

        # 1) PG agent_messages
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                rows = db.fetchall(
                    """
                    SELECT id, role, content, tool_call_id, tool_calls,
                           tool_name, timestamp, finish_reason, reasoning,
                           reasoning_details, duration_ms, turn_meta,
                           display_kind, display_metadata
                      FROM agent_messages
                     WHERE session_id = %s
                       -- v0.20 display projection (#80622): 'hidden' rows are
                       -- presentation-suppressed transcript bookkeeping
                       -- (compaction-summary carriers, interrupt scaffolds).
                       -- Upstream clients get the flag and drop the row; the
                       -- SPA has no such filter, so drop server-side or a
                       -- summary carrier renders as a real user message.
                       AND (display_kind IS DISTINCT FROM 'hidden')
                     ORDER BY timestamp, id
                    """,
                    (str(conv_id),),
                ) or []
            if rows:
                msgs = []
                for r in rows:
                    msg = {
                        "id": r.get("id"),
                        "session_id": str(conv_id),
                        "role": r.get("role"),
                        "content": r.get("content"),
                        "timestamp": r.get("timestamp"),
                    }
                    for k in ("tool_calls", "tool_call_id", "tool_name",
                              "reasoning", "reasoning_details", "finish_reason",
                              "duration_ms", "turn_meta",
                              # Non-hidden bookkeeping kinds (model_switch,
                              # auto_continue, …) ride along so the SPA can
                              # demote/badge them like the desktop does.
                              "display_kind", "display_metadata"):
                        val = r.get(k)
                        if val is not None:
                            msg[k] = val
                    msgs.append(msg)
                return JSONResponse({"data": msgs, "source": "agent_messages"})
        except Exception:
            logger.exception("get_session_messages_pg_failed conv=%s", conv_id)

        # No PG row → empty. Charter v2 dev phase: agent_messages
        # is the single source; legacy sqlite + JSON fallbacks dropped.
        return JSONResponse({"data": []})

    @app.get("/api/search", include_in_schema=False)
    async def search_sessions(q: str, request: Request, limit: int = 20):
        """Full-text search over agent messages — Charter §一原语 ② projection.

        Uses the ``idx_interactions_search_tsv`` GIN index from alembic 0027
        for to_tsvector matching, filtered to the calling user's actor.
        """
        user_id = require_bearer_auth(request, cfg)
        if not q or not q.strip():
            return JSONResponse({"error": "query required"}, status_code=400)
        capped = min(max(1, int(limit)), 50)
        try:
            from hermes.crm.db import db_session
            uid_int = int(user_id) if str(user_id).isdigit() else 0
            with db_session() as db:
                rows = db.fetchall(
                    """
                    SELECT id::text AS interaction_id,
                           content,
                           occurred_at,
                           ts_rank(
                             to_tsvector('simple', coalesce(search_text, '')),
                             plainto_tsquery('simple', %s)
                           ) AS rank
                      FROM interactions
                     WHERE kind = 'agent.message'
                       AND actors @> %s::jsonb
                       AND to_tsvector('simple', coalesce(search_text, ''))
                           @@ plainto_tsquery('simple', %s)
                     ORDER BY rank DESC, occurred_at DESC
                     LIMIT %s
                    """,
                    (q.strip(),
                     json.dumps([{"type": "user", "id": uid_int}]),
                     q.strip(),
                     capped),
                ) or []
            results = []
            for r in rows:
                c = r.get("content") or {}
                results.append({
                    "session_id": c.get("session_id"),
                    "message_id": c.get("message_id"),
                    "role": c.get("role"),
                    "snippet": (str(c.get("text") or "")[:300]),
                    "timestamp": (
                        r["occurred_at"].timestamp()
                        if r.get("occurred_at") else None
                    ),
                    "rank": float(r.get("rank") or 0.0),
                })
            return JSONResponse({"results": results})
        except Exception as exc:
            logger.exception("search_sessions_failed q=%r", q)
            return JSONResponse({"results": [], "error": str(exc)})

    @app.get("/api/analytics", include_in_schema=False)
    async def get_analytics(request: Request, days: int = 30):
        """Token / session / cost analytics — Charter §一原语 ③ projection.

        Reads from ``agent_session_metrics`` (alembic 0030 trigger keeps it
        in sync with interactions). All numbers are accurate as of the
        most recent agent.message INSERT.
        """
        user_id = require_bearer_auth(request, cfg)
        empty = {"daily": [], "by_model": [], "totals": {
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "reasoning_tokens": 0, "estimated_cost_usd": 0.0, "sessions": 0,
        }}
        capped_days = max(1, min(int(days), 365))
        try:
            from hermes.crm.db import db_session
            uid_int = int(user_id) if str(user_id).isdigit() else 0
            with db_session() as db:
                daily = db.fetchall(
                    """
                    SELECT to_char(date_trunc('day', started_at), 'YYYY-MM-DD') AS day,
                           SUM(input_tokens)::BIGINT       AS input_tokens,
                           SUM(output_tokens)::BIGINT      AS output_tokens,
                           SUM(cache_read_tokens)::BIGINT  AS cache_read_tokens,
                           SUM(reasoning_tokens)::BIGINT   AS reasoning_tokens,
                           SUM(estimated_cost_usd)::FLOAT  AS estimated_cost_usd,
                           COUNT(*)::INT                   AS sessions
                      FROM agent_session_metrics
                     WHERE user_id = %s
                       AND started_at > now() - (%s || ' days')::interval
                     GROUP BY day
                     ORDER BY day
                    """,
                    (uid_int, str(capped_days)),
                ) or []
                by_model = db.fetchall(
                    """
                    SELECT model,
                           SUM(input_tokens)::BIGINT       AS input_tokens,
                           SUM(output_tokens)::BIGINT      AS output_tokens,
                           SUM(estimated_cost_usd)::FLOAT  AS estimated_cost_usd,
                           COUNT(*)::INT                   AS sessions
                      FROM agent_session_metrics
                     WHERE user_id = %s
                       AND started_at > now() - (%s || ' days')::interval
                       AND model IS NOT NULL
                     GROUP BY model
                     ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
                    """,
                    (uid_int, str(capped_days)),
                ) or []
                totals_row = db.fetchone(
                    """
                    SELECT COALESCE(SUM(input_tokens), 0)::BIGINT      AS input_tokens,
                           COALESCE(SUM(output_tokens), 0)::BIGINT     AS output_tokens,
                           COALESCE(SUM(cache_read_tokens), 0)::BIGINT AS cache_read_tokens,
                           COALESCE(SUM(reasoning_tokens), 0)::BIGINT  AS reasoning_tokens,
                           COALESCE(SUM(estimated_cost_usd), 0)::FLOAT AS estimated_cost_usd,
                           COUNT(*)::INT                               AS sessions
                      FROM agent_session_metrics
                     WHERE user_id = %s
                       AND started_at > now() - (%s || ' days')::interval
                    """,
                    (uid_int, str(capped_days)),
                ) or empty["totals"]
            return JSONResponse({
                "daily": [dict(r) for r in daily],
                "by_model": [dict(r) for r in by_model],
                "totals": dict(totals_row),
            })
        except Exception as exc:
            logger.exception("get_analytics_failed days=%s", days)
            return JSONResponse({**empty, "error": str(exc)})

    @app.get("/api/me", include_in_schema=False)
    async def get_me(request: Request):
        user_id = require_bearer_auth(request, cfg)
        account = app.state.auth_store.get_user(user_id)
        if account is None:
            return JSONResponse({"user_id": user_id, "email": None, "is_admin": False})
        # Keep the minimal legacy shape so older callers don't break.
        payload = {
            "user_id": account.id,
            "email": getattr(account, "email", None),
            "is_admin": getattr(account, "is_admin", False),
        }
        # Enrich with CRM profile fields (department/role joined). Silently
        # skip if CRM schema isn't present — the minimal shape still works.
        try:
            from hermes.crm.db import db_session
            uid = int(account.id)
            with db_session() as db:
                row = db.fetchone(
                    """
                    SELECT u.id, u.username, u.realname, u.email, u.mobile,
                           u.avatar, u.description, u.plan, u.is_admin,
                           u.status, u.created_at,
                           u.department_id, u.role_id, u.report_to_ids,
                           d.name AS department_name,
                           r.name AS role_name, r.description AS role_description
                      FROM users u
                      LEFT JOIN departments d ON d.id = u.department_id
                      LEFT JOIN roles r       ON r.id = u.role_id
                     WHERE u.id = %s
                    """,
                    (uid,),
                )
                report_to: list[dict] = []
                if row:
                    raw_ids = row.get("report_to_ids") or []
                    if isinstance(raw_ids, list) and raw_ids:
                        mgr_rows = db.fetchall(
                            "SELECT id, realname, username FROM users "
                            "WHERE id = ANY(%s) AND deleted = FALSE",
                            ([int(x) for x in raw_ids if x is not None],),
                        )
                        report_to = [
                            {
                                "id": int(m["id"]),
                                "name": (m.get("realname") or m.get("username") or "").strip(),
                            }
                            for m in (mgr_rows or [])
                        ]
            if row:
                payload.update({
                    "id": int(row["id"]),
                    "username": row.get("username"),
                    "realname": (row.get("realname") or "").strip() or None,
                    "mobile": (row.get("mobile") or "").strip() or None,
                    "avatar": row.get("avatar"),
                    "description": (row.get("description") or "").strip() or None,
                    "plan": row.get("plan"),
                    "status": int(row.get("status") or 0),
                    "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                    "department": (
                        {"id": int(row["department_id"]), "name": row.get("department_name") or ""}
                        if row.get("department_id") else None
                    ),
                    "role": (
                        {
                            "id": int(row["role_id"]),
                            "name": row.get("role_name") or "",
                            "description": (row.get("role_description") or "").strip() or None,
                        }
                        if row.get("role_id") else None
                    ),
                    "report_to": report_to,
                })
        except Exception:
            logger.debug("get_me: CRM enrichment skipped", exc_info=True)
        # Profile-usage badge: used = active profile count; limit = effective
        # quota cap (None = unlimited). Frontend uses this to disable the
        # "new profile" button when at capacity.
        try:
            from hermes_cloud.app.permissions import resolve_profile_quota
            active = [
                p for p in app.state.profile_store.list_profiles(user_id=user_id)
                if p.is_active
            ]
            payload["profile_usage"] = {
                "used": len(active),
                "limit": resolve_profile_quota(user_id),  # None = unlimited
            }
        except Exception:
            logger.debug("get_me: profile_usage enrichment skipped", exc_info=True)

        # Phase 5.2 — is_manager flag for SPA scope segments. The SPA's
        # list / dashboard pages render the (我的 / 团队 / 全部) segment
        # only when this is True. Source-of-truth: ``viewable_user_ids``
        # at scope='team' returns more than {self} iff the caller manages
        # at least one subordinate (recursive through dept.parent_id).
        # Using viewable_user_ids here keeps the SPA flag and the actual
        # query-layer enforcement in lock-step.
        try:
            from hermes.crm.db import db_session as _ds
            from hermes_cloud.app.scope import viewable_user_ids
            from hermes_cloud.app.scope.audit import record_scope_access
            uid = int(payload.get("id") or user_id or 0)
            with _ds() as _mgr_db:
                allowed = viewable_user_ids(
                    _mgr_db, caller_id=uid, scope="team",
                )
                # Plan §7 CI #3: any scope='team' query writes audit_log,
                # even a metadata probe. is_manager_probe is the
                # resource_kind so dashboards can split it from real reads.
                record_scope_access(
                    _mgr_db, caller_id=uid, scope_kind="team",
                    resource_kind="is_manager_probe",
                )
                _mgr_db.commit()
            payload["is_manager"] = len(allowed - {uid}) > 0
        except Exception:
            logger.debug("get_me: is_manager probe skipped", exc_info=True)
            payload["is_manager"] = False
        return JSONResponse(payload)

    @app.put("/api/me", include_in_schema=False)
    async def put_me(request: Request):
        """Self-edit for non-privileged fields.

        Users can update their own ``realname``, ``mobile``, ``description``.
        Everything else (email, role, department, is_admin, status, plan) is
        admin-only via ``/api/admin/users/{id}``.
        """
        user_id = require_bearer_auth(request, cfg)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be JSON object")

        allowed = {"realname", "mobile", "description"}
        updates: dict = {}
        for k in allowed:
            if k in body:
                v = body[k]
                if v is None:
                    updates[k] = None
                else:
                    s = str(v).strip()
                    # Soft length caps matching column widths.
                    if k == "realname" and len(s) > 100:
                        raise HTTPException(status_code=400, detail="realname too long")
                    if k == "mobile" and len(s) > 100:
                        raise HTTPException(status_code=400, detail="mobile too long")
                    updates[k] = s or None
        if not updates:
            raise HTTPException(status_code=400, detail="no editable fields in body")

        try:
            from hermes.crm.db import db_session
            uid = int(user_id)
            sets = ", ".join(f"{k} = %s" for k in updates)
            params = list(updates.values()) + [uid]
            with db_session() as db:
                db.execute(f"UPDATE users SET {sets} WHERE id = %s", tuple(params))
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("put_me: update failed for user=%s", user_id)
            raise HTTPException(status_code=500, detail=f"update failed: {e}")
        return await get_me(request)

    @app.post("/api/me/password", include_in_schema=False)
    async def change_my_password(request: Request):
        """Self-service password change for the authenticated user.

        Verifies the current password before applying the new one — admins
        reset other users' passwords via ``/api/admin/users/{id}/reset-password``.
        """
        user_id = require_bearer_auth(request, cfg)
        body = await _parse_json_body(request)
        current_password = str(body.get("current_password", ""))
        new_password = str(body.get("new_password", ""))

        account = app.state.auth_store.get_user(user_id)
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Not authenticated.",
                                  "type": "authentication_error", "code": "not_authenticated"}},
            )
        verified = app.state.auth_store.authenticate_with_password(
            account.email, current_password
        )
        if verified is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Current password is incorrect.",
                                  "type": "authentication_error", "code": "invalid_current_password"}},
            )
        if len(new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "Password must be at least 8 characters.",
                                  "type": "invalid_request_error", "code": "password_too_short"}},
            )
        app.state.auth_store.reset_password(user_id, new_password)
        return {"ok": True}

    @app.get("/api/me/permissions", include_in_schema=False)
    async def get_my_permissions(request: Request):
        """Return the effective permission matrix for the caller's role.

        Returns two complementary views in one payload:

        * ``grants`` — flat ``["module.action", ...]`` list. Preferred by
          the SPA for nav/button gating (``grants.includes("agents.index")``).
          For admins this is fully enumerated (one entry per known
          module×permission pair), so the frontend never needs to
          special-case ``is_admin``.
        * ``modules`` — structured list with ``kind`` / ``title`` /
          ``description`` so the role-permission UI can render tabs and
          tooltips without a second round-trip.

        ``role`` is kept for the header badge in the admin pages.
        """
        user_id = require_bearer_auth(request, cfg)
        account = app.state.auth_store.get_user(user_id)
        if account is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        try:
            from hermes.crm.db import db_session
            from hermes_cloud.app.permissions import collect_user_grants
            uid = int(user_id)
            payload = collect_user_grants(uid)
            # Fetch role info separately (collect_user_grants doesn't expose it;
            # header badge in AdminRoles reads this).
            role_info = None
            with db_session() as db:
                urow = db.fetchone(
                    "SELECT role_id FROM users WHERE id = %s", (uid,)
                )
                role_id = urow.get("role_id") if urow else None
                if role_id:
                    role_row = db.fetchone(
                        "SELECT id, name, description FROM roles WHERE id = %s",
                        (int(role_id),),
                    )
                    if role_row:
                        role_info = {
                            "id": int(role_row["id"]),
                            "name": role_row.get("name") or "",
                            "description": (role_row.get("description") or "").strip() or None,
                        }
                # Legacy nested shape still used by the AdminRoles matrix UI.
                perm_rows = db.fetchall(
                    "SELECT id, code, name FROM permissions ORDER BY id"
                ) or []
                rp_rows = []
                if role_id and not payload["is_admin"]:
                    rp_rows = db.fetchall(
                        "SELECT module_id, permission_id, has_permission "
                        "FROM role_permissions WHERE role_id = %s",
                        (int(role_id),),
                    ) or []
            granted_set = {
                (int(g["module_id"]), int(g["permission_id"]))
                for g in rp_rows if g.get("has_permission")
            }
            modules_nested = [
                {
                    "id": m["id"],
                    "name": m["name"],
                    "title": m.get("title"),
                    "kind": m.get("kind") or "crm",
                    "description": m.get("description"),
                    "permissions": [
                        {
                            "code": p.get("code") or "",
                            "name": p.get("name") or "",
                            "granted": payload["is_admin"]
                                or (int(m["id"]), int(p["id"])) in granted_set,
                        }
                        for p in perm_rows
                    ],
                }
                for m in payload["modules"]
            ]
            return JSONResponse({
                "is_admin": payload["is_admin"],
                "role": role_info,
                "grants": payload["grants"],
                "modules": modules_nested,
            })
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("get_my_permissions: failed user=%s", user_id)
            raise HTTPException(status_code=500, detail=f"fetch failed: {e}")

    # ------------------------------------------------------------------
    # Memory — read/write MEMORY.md and USER.md from profile dir
    # ------------------------------------------------------------------
    def _profile_dir(user_id: str) -> Path:
        hermes_home = Path(cfg.hermes_home_root) if cfg.hermes_home_root else Path.home() / ".hermes"
        return hermes_home / "profiles" / user_id

    def _normalize_session_source(source: str | None) -> str:
        """Map raw agent_sessions.source to the canonical surface name
        the SPA's icon map uses. Aliases collapse hermes-internal
        naming (``weixin``, ``lark``) into SPA-facing values
        (``wechat``, ``feishu``)."""
        value = str(source or "").strip().lower()
        # Canonicalize platform aliases the SPA's SOURCE_BADGE map knows.
        _ALIASES = {
            "webui": "web", "browser": "web",
            "weixin": "wechat",
            "wecom_ilink": "wechat",
            "wecom": "wechat",
            "lark": "feishu",
            "api_server": "web",   # chatbox/copilot/decisions/dashboards/TUI
            "gateway": "web",
        }
        return _ALIASES.get(value, value) or "unknown"

    # Charter v2 §定律 4 — only sessions produced by a user-facing
    # surface (or an explicitly agent-driven one like auto_drive)
    # belong in the ChatInbox. Utility / generator LLM calls
    # (question_generator / brief_generator / daily_brief /
    # record_question / copilot_send / unknown) are NOT conversations
    # — they're cached LLM-utility outputs that incorrectly show up
    # in agent_session_metrics because hermes registers every chat
    # completion as a "session". Whitelist gates the orphan-merge
    # branch so those rows stop polluting the inbox.
    # Charter v2 §定律 4 — values written to ``agent_sessions.source``
    # by hermes itself. chatbox/copilot/decisions/dashboards/TUI all
    # show up as ``api_server`` (hermes' OpenAI-compat HTTP server).
    # IM channels write their platform name. Utility cron jobs and
    # internal generators are excluded (they're not conversations).
    _INBOX_SOURCE_WHITELIST = frozenset({
        "api_server",          # chatbox, copilot, decisions, dashboards, TUI
        "gateway",             # generic gateway-spawned (legacy)
        "weixin", "wecom", "wecom_ilink",
        "feishu", "lark",
        "dingtalk", "slack", "telegram", "discord", "email", "sms",
    })

    def _is_inbox_worthy_source(source: str | None) -> bool:
        return _normalize_session_source(source) in _INBOX_SOURCE_WHITELIST

    def _sync_config_to_profile(
        user_id: str,
        profile_id: str | None = None,
        *,
        subject_key: str | None = None,
    ) -> None:
        """Sync cloud model + toolsets config into the hermes profile config.yaml.

        hermes reads config.yaml on every request (no in-process cache), so this
        takes effect immediately without restarting the hermes process.

        This avoids any need to pass X-Hermes-* per-request override headers,
        keeping hermes gateway code unmodified.

        Config lookups are still keyed by ``user_id`` (per-profile config is
        Task 4 of the multi-profile plan), but the file is written into the
        *profile's* config.yaml — which for the default profile is the same
        path as before, and for non-default profiles lives under the profile's
        own HERMES_HOME directory.
        """
        import json as _json
        import yaml as _yaml

        target_id = profile_id if profile_id is not None else user_id
        profile_cfg_path = _profile_dir(target_id) / "config.yaml"
        profile_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if profile_cfg_path.exists():
            try:
                existing = _yaml.safe_load(profile_cfg_path.read_text()) or {}
            except Exception:
                existing = {}

        def _normalize_provider_api_key(provider_name: str, base_url: str | None, api_key: str | None) -> str | None:
            base = str(base_url or "")
            name = str(provider_name or "")
            if api_key:
                return api_key
            if name in {"opencode-zen", "opencode-go"} or "opencode.ai/zen" in base:
                return "public"
            return api_key

        # --- Model / provider ---
        # Non-default profiles that haven't been configured inherit the user's
        # default-profile model. Without this fallback, a messaging-bound
        # profile (e.g. feishu → research_profile) boots hermes with
        # model="" → 400. See _resolve_effective_model_cfg for the policy.
        model_cfg = _resolve_effective_model_cfg(
            app.state.config_store, user_id, target_id,
        )
        if model_cfg and (model_cfg.model or model_cfg.api_key or model_cfg.base_url):
            model_section: dict = {}
            if model_cfg.model:
                model_section["default"] = model_cfg.model
                model_section["model"] = model_cfg.model
            normalized_api_key = _normalize_provider_api_key("", model_cfg.base_url, model_cfg.api_key)
            if normalized_api_key:
                model_section["api_key"] = normalized_api_key
            # Resolve provider id + api_format from the user's providers_json
            # in a single pass. The SPA writes one entry per provider
            # (key = provider id like "minimax", "deepseek", "zhipu"); the
            # active model's base_url uniquely identifies which entry the
            # user picked. Using the SPA-supplied id is the only correct
            # source of truth — reverse-engineering from URL substrings
            # collapses every non-opencode provider to "custom", which then
            # makes hermes' auxiliary client guess the wire format and
            # rewrite paths (e.g. /anthropic → /v1), yielding 404s on
            # providers whose OpenAI surface isn't at the standard path
            # (MiniMax-CN, Zhipu, etc.).
            resolved_provider_id: str | None = None
            api_format_resolved: str | None = None
            supports_vision_resolved: bool = False
            if model_cfg.providers_json:
                try:
                    _pj = _json.loads(model_cfg.providers_json)
                    _base_norm = str(model_cfg.base_url or "").rstrip("/")
                    for _pid, _p in (_pj.get("providers") or {}).items():
                        if (str(_p.get("base_url") or "").rstrip("/") == _base_norm):
                            resolved_provider_id = str(_pid) or None
                            api_format_resolved = str(_p.get("api_format") or "").lower() or None
                            # 把该模型的 supports_vision 声明传到 config.yaml 顶层
                            # model.supports_vision(hermes _model_supports_vision 的
                            # 解析顺序①),让视觉模型(M3 等)的内联图片直接喂给模型,
                            # 不走「描述回退/vision_analyze 下载 URL」那条会 403 的路。
                            for _m in (_p.get("models") or []):
                                if (isinstance(_m, dict)
                                        and str(_m.get("id")) == str(model_cfg.model)
                                        and _m.get("supports_vision")):
                                    supports_vision_resolved = True
                                    break
                            break
                except Exception:
                    pass
            if supports_vision_resolved:
                model_section["supports_vision"] = True

            if model_cfg.base_url:
                model_section["base_url"] = model_cfg.base_url
                # Prefer the SPA-supplied provider id when we matched it
                # against providers_json. Fall back to URL substring
                # detection for legacy configs that never wrote
                # providers_json, and finally to "custom" for truly
                # unknown endpoints.
                #
                # Why ``custom:<id>`` (not bare ``<id>``): when the SPA
                # id matches a canonical built-in (minimax / deepseek /
                # moonshot / openai / anthropic / ...), hermes'
                # ``_get_named_custom_provider`` guard at
                # runtime_provider.py:432 deliberately skips the
                # custom_providers entry so the built-in env-var path
                # (MINIMAX_API_KEY etc.) handles it. We don't have env
                # vars in cloud — the user's API key is inline in the
                # custom_providers entry — so we need the ``custom:``
                # prefix to bypass that guard and route through the
                # named entry. Opencode (free/public) stays as-is
                # because its built-in path works without a user key.
                base_lower = model_cfg.base_url.lower()
                if resolved_provider_id in ("opencode-zen", "opencode-go"):
                    model_section["provider"] = resolved_provider_id
                elif resolved_provider_id:
                    model_section["provider"] = f"custom:{resolved_provider_id}"
                elif "opencode.ai/zen/go" in base_lower:
                    model_section["provider"] = "opencode-go"
                elif "opencode.ai/zen" in base_lower:
                    model_section["provider"] = "opencode-zen"
                else:
                    model_section["provider"] = "custom"
            # Carry api_mode (Anthropic vs OpenAI format) onto the
            # top-level model section. hermes reads api_mode from the
            # model: block directly, so the custom_providers entry alone
            # isn't enough — without this, every "custom" provider
            # defaulted to chat_completions and 404'd against /anthropic
            # URLs. The legacy "/anthropic/" path heuristic is kept as a
            # last-resort fallback for configs that never picked the radio.
            base_lower = (model_cfg.base_url or "").lower()
            if api_format_resolved == "anthropic" or "/anthropic/" in base_lower:
                model_section["api_mode"] = "anthropic_messages"
                # Anthropic SDK auto-appends /v1/messages. If the user's
                # configured base_url already ends in /v1 (a common mis-
                # configuration mirroring the OpenAI-style URL), strip it
                # so we don't end up POSTing to /<path>/v1/v1/messages → 404.
                bu = (model_section.get("base_url") or "").rstrip("/")
                if bu.endswith("/v1"):
                    model_section["base_url"] = bu[:-3]
            existing["model"] = model_section

            # --- S0: web_search 跟随模型 provider ---
            # 各家用各家自带联网(同一把 per-user key);无联网的家
            # (deepseek/ollama/聚合器…)回落全局 MiniMax CLI 搜索
            # (MINIMAX_CODING_API_KEY)。详见 spec web-search-follow-model。
            # minimax 始终用全局 coding key(mmx 需 coding-plan key,用户
            # 模型 key 未必是),故 minimax 不写 per-user search_key。
            _SEARCH_BACKEND_BY_PROVIDER = {
                "minimax": "minimax", "zhipu": "glm", "moonshot": "kimi",
                "qwen": "qwen", "openai": "openai", "anthropic": "anthropic",
            }
            _web = dict(existing.get("web") or {})
            _sb = _SEARCH_BACKEND_BY_PROVIDER.get(resolved_provider_id or "")
            if _sb and _sb != "minimax":
                _web["search_backend"] = _sb
                if model_cfg.api_key:
                    _web["search_key"] = model_cfg.api_key
                if model_cfg.base_url:
                    _web["search_base_url"] = model_cfg.base_url
                # chat-with-search 类(kimi/qwen/openai/anthropic)需要 model id
                # 发联网 chat 调用;glm 是独立 API 不需要,带着也无害。
                if model_cfg.model:
                    _web["search_model"] = model_cfg.model
            else:
                # MiniMax 模型 或 无联网家 → 全局 mmx 兜底(不带 per-user key)
                _web["search_backend"] = "minimax"
                _web.pop("search_key", None)
                _web.pop("search_base_url", None)
                _web.pop("search_model", None)
            existing["web"] = _web

            # Convert providers_json → hermes custom_providers list
            if model_cfg.providers_json:
                try:
                    pcfg = _json.loads(model_cfg.providers_json)
                    providers = pcfg.get("providers", {})
                    if providers:
                        custom_providers = []
                        for prov_id, prov_data in providers.items():
                            if not prov_data.get("enabled", True):
                                continue
                            provider_api_key = _normalize_provider_api_key(
                                prov_id,
                                prov_data.get("base_url", ""),
                                prov_data.get("api_key", ""),
                            )
                            # Map the SPA's per-provider api_format radio
                            # ("Anthropic 兼容" vs "OpenAI 兼容") to the
                            # hermes api_mode value. Previously hard-coded
                            # to chat_completions, which 404'd whenever the
                            # admin had picked the Anthropic format and a
                            # base_url like /anthropic/v1 (the request path
                            # would resolve to /anthropic/v1/chat/completions
                            # which doesn't exist on the upstream provider).
                            api_format = str(prov_data.get("api_format") or "openai").lower()
                            api_mode = (
                                "anthropic_messages" if api_format == "anthropic"
                                else "chat_completions"
                            )
                            # ``models``: list of model ids served by this
                            # provider. Written so hermes' api_server platform
                            # can resolve a per-request body.model override
                            # back to the entry's credentials, enabling the
                            # SPA ModelPicker to actually switch providers
                            # mid-session (see hermes-patches: api_server
                            # per-request switch_model).
                            model_ids = [
                                m.get("id") for m in (prov_data.get("models") or [])
                                if isinstance(m, dict) and m.get("id")
                            ]
                            entry = {
                                "name": prov_id,
                                "base_url": prov_data.get("base_url", ""),
                                "api_key": provider_api_key or "",
                                "api_mode": api_mode,
                                "models": model_ids,
                            }
                            # Provider-specific request fields (hermes merges
                            # them into every main-model call via
                            # agent_init._merge_custom_provider_extra_body).
                            _eb = prov_data.get("extra_body")
                            if isinstance(_eb, dict) and _eb:
                                entry["extra_body"] = _eb
                            elif "longcat" in str(entry["base_url"]).lower():
                                # LongCat-2.0 thinks-by-default: 3-6s of hidden
                                # reasoning before every reply. 2026-07-07
                                # decision (user): thinking OFF for chat — the
                                # raw chain-of-thought isn't displayed anywhere
                                # anymore, so its only remaining effect was
                                # latency. providers_json may override by
                                # setting its own extra_body on the provider.
                                entry["extra_body"] = {
                                    "chat_template_kwargs": {"enable_thinking": False}
                                }
                            custom_providers.append(entry)
                        if custom_providers:
                            existing["custom_providers"] = custom_providers
                            # 模型角色(auxiliary + MoA)统一由 model_roles 从
                            # providers_json 派生，key 级合并进 config.yaml。
                            # 见 docs/superpowers/specs/2026-07-06-model-roles-settable-design.md
                            model_roles.apply_to_config(existing, pcfg)
                except Exception:
                    # 保留旧 aux seed 曾有的可观测性:providers_json 解析或
                    # 模型角色派生若出错,别静默吞掉(仍不 raise——同步是尽力而为)。
                    logger.debug("providers_json → config sync failed", exc_info=True)

        # --- Toolsets (platform_toolsets.*) ---
        # The user's toolset choice (e.g. "crm enabled") must apply to
        # EVERY surface the agent answers on — the SPA chatbox
        # (api_server) AND every messaging channel (feishu / weixin /
        # wecom / ...). hermes' _get_platform_tools keys the tool list
        # by platform; a platform with no entry falls back to its bare
        # default toolset, which excludes crm. So a feishu inbound
        # whose profile only had platform_toolsets.api_server would see
        # the agent reply "crm 工具不可用". Write the same list for
        # api_server + all channels to keep tool availability uniform.
        _TOOLSET_PLATFORMS = (
            "api_server", "feishu", "weixin", "wecom",
            "telegram", "discord", "slack", "dingtalk",
            # "cli" too: a `hermes -z/-chat` run inside the profile is the
            # same agent answering the same user. Without an entry the CLI
            # falls back to the bare default toolset (no crm_*) and the
            # agent improvises — measured 2026-07-07: the same question that
            # took the SPA 49.7s took the CLI 467s across 18 rounds of
            # hand-written psql because crm_search wasn't registered.
            "cli",
        )
        toolsets_cfg = _resolve_effective_toolsets_cfg(
            app.state.config_store, user_id, target_id,
        )
        if toolsets_cfg and toolsets_cfg.toolsets:
            pt = existing.setdefault("platform_toolsets", {})
            for _plat in _TOOLSET_PLATFORMS:
                pt[_plat] = list(toolsets_cfg.toolsets)
        else:
            # Remove stale overrides so hermes uses its per-platform
            # default toolsets.
            pt = existing.get("platform_toolsets", {})
            for _plat in _TOOLSET_PLATFORMS:
                pt.pop(_plat, None)

        # Charter v2 Phase 3 (2026-05-06): A/B router LoRA injection
        # removed — RL adapter routing is gone, profiles always run on
        # their configured base model.

        # Charter §一原语 ⑤ Action Gateway — enable the bundled
        # ``cloud-approval`` plugin so /approve and /reject slash
        # commands work natively on Discord / Slack / Telegram (PR
        # #51ca5759 in upstream surfaces them as platform-native
        # command pickers). The plugin's source ships via
        # hermes-patches/11; this just opts the profile into running
        # it. Idempotent — appends only when not already present.
        plugins_section = existing.get("plugins")
        if not isinstance(plugins_section, dict):
            plugins_section = {}
        enabled = plugins_section.get("enabled")
        if not isinstance(enabled, list):
            enabled = []
        if "cloud-approval" not in enabled:
            enabled.append("cloud-approval")
            plugins_section["enabled"] = enabled
            existing["plugins"] = plugins_section
        # Charter §一原语 ④ — also enable the bundled ``crm_tools``
        # plugin so the per-profile gateway exposes crm_search /
        # crm_write / task_create / etc. as real LLM-callable tools.
        # Without this, the plugin discovery flag drops it ("not in
        # plugins.enabled") and the main agent can't call task_create
        # directly even though the cloud-side override path would
        # accept it. Cloud-spawned profiles always want CRM access —
        # there's no use case for an enabled hermes_cloud profile that
        # disables CRM tools.
        if "crm_tools" not in enabled:
            enabled.append("crm_tools")
            plugins_section["enabled"] = enabled
            existing["plugins"] = plugins_section

        # Charter §一原语 ⑧ Trust Engine + 定律 5 (AI 做人审批) ─
        # ensure ``approvals.cron_mode`` is explicitly set on every
        # cloud-managed profile, defaulting to "deny" if the user has
        # not chosen otherwise. Hermes upstream v0.11.0+ exposes this
        # knob (PR #762f7e97). Writing it explicitly makes the policy
        # auditable in profile config.yaml and prevents a silent
        # upstream default flip from auto-approving dangerous
        # cron-driven commands. We respect a user-set value (admin may
        # opt into "approve" for a trusted sandbox) and only write the
        # default when the key is absent.
        approvals = existing.get("approvals")
        if not isinstance(approvals, dict):
            approvals = {}
        if "cron_mode" not in approvals:
            approvals["cron_mode"] = "deny"
        # Interactive approval mode — same rationale as cron_mode above, for
        # the human-present path. Upstream's default is "smart": an auxiliary
        # LLM guardian judges each flagged command and silently auto-approves
        # the ones it deems low-risk. Observed on prod 2026-09-04: a real
        # ``rm -rf`` was classified "delete in root path" and then
        # auto-approved with no card shown to the user. The operator's choice
        # for this deployment is that a human sees EVERY dangerous command, so
        # pin "manual" — a guardian model's judgement is not an acceptable
        # substitute for the owner's, and it is one prompt-injection away from
        # approving something it shouldn't. Respect an explicit user-set value
        # (a trusted sandbox may opt back into "smart"/"off"); only write the
        # default when the key is absent.
        if "mode" not in approvals:
            approvals["mode"] = "manual"
        existing["approvals"] = approvals

        profile_cfg_path.write_text(_yaml.dump(existing, default_flow_style=False))
        logger.debug("synced config to profile uid=%s model=%s",
                     user_id, model_cfg.model if model_cfg else None)

    @app.get("/api/memory")
    def get_memory(request: Request):
        user_id = require_bearer_auth(request, cfg)
        mem_dir = _profile_dir(user_id) / "memories"
        def _read(fname: str) -> str:
            p = mem_dir / fname
            return p.read_text(encoding="utf-8") if p.exists() else ""
        return JSONResponse({
            "memory": _read("MEMORY.md"),
            "user":   _read("USER.md"),
        })

    @app.put("/api/memory")
    async def put_memory(request: Request):
        user_id = require_bearer_auth(request, cfg)
        body = await _parse_json_body(request)
        mem_dir = _profile_dir(user_id) / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        results = {}
        if "memory" in body:
            (mem_dir / "MEMORY.md").write_text(body["memory"], encoding="utf-8")
            results["memory"] = "saved"
        if "user" in body:
            (mem_dir / "USER.md").write_text(body["user"], encoding="utf-8")
            results["user"] = "saved"
        return JSONResponse(results)

    # ------------------------------------------------------------------
    # Personality (agent.system_prompt) — per-profile config.yaml entry
    # ------------------------------------------------------------------
    def _read_profile_config(profile_id: str) -> dict:
        import yaml as _yaml
        path = _profile_dir(profile_id) / "config.yaml"
        if not path.exists():
            return {}
        try:
            return _yaml.safe_load(path.read_text()) or {}
        except Exception:
            return {}

    def _write_profile_config(profile_id: str, data: dict) -> None:
        import yaml as _yaml
        path = _profile_dir(profile_id) / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_yaml.dump(data, default_flow_style=False, allow_unicode=True))

    # ------------------------------------------------------------------
    # Channel-creds helpers (per-user-channel-bot plan).
    #
    # Channel credentials live in the profile's config.yaml under
    # ``platforms.<channel>.extra``. Hermes' native adapter reads YAML
    # on every request, so changes take effect on the next message —
    # no DB, no encryption layer, no sync. Implementation in
    # ``hermes_cloud.app.channel_config`` so the logic is unit-testable
    # without a FastAPI fixture; here we thin-wrap to expose it under
    # the existing ``_profile_dir`` resolver.
    #
    # See ``docs/superpowers/plans/2026-05-19-channel-per-user-bot.md``.
    # ------------------------------------------------------------------
    from hermes_cloud.app.channel_config import (
        read_channel_config as _read_channel_config_impl,
        write_channel_config as _write_channel_config_impl,
        read_email_channel_config as _read_email_channel_config_impl,
        write_email_channel_config as _write_email_channel_config_impl,
    )

    def _read_channel_config(profile_id: str, channel: str) -> dict:
        # Email is configured in the profile's .env (hermes' EmailAdapter
        # reads EMAIL_* from env), not config.yaml — see channel_config.py.
        if channel == "email":
            return _read_email_channel_config_impl(_profile_dir(profile_id))
        return _read_channel_config_impl(_profile_dir(profile_id), channel)

    def _write_channel_config(profile_id: str, channel: str, extra: dict) -> None:
        if channel == "email":
            _write_email_channel_config_impl(_profile_dir(profile_id), extra)
            return
        _write_channel_config_impl(_profile_dir(profile_id), channel, extra)

    @app.get("/api/user-config/personality")
    def get_user_personality(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id)
        data = _read_profile_config(profile_id or user_id)
        system_prompt = ""
        agent = data.get("agent")
        if isinstance(agent, dict):
            system_prompt = agent.get("system_prompt") or ""
        return {"system_prompt": system_prompt}

    @app.put("/api/user-config/personality")
    async def put_user_personality(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id) or user_id
        body = await _parse_json_body(request)
        system_prompt = body.get("system_prompt", "")
        if not isinstance(system_prompt, str):
            raise HTTPException(
                status_code=400,
                detail={"error": {"message": "system_prompt must be a string",
                                  "type": "invalid_request_error",
                                  "code": "invalid_field"}},
            )
        data = _read_profile_config(profile_id)
        agent = data.get("agent")
        if not isinstance(agent, dict):
            agent = {}
        agent["system_prompt"] = system_prompt
        data["agent"] = agent
        _write_profile_config(profile_id, data)
        return {"system_prompt": system_prompt}

    # ------------------------------------------------------------------
    # 新用户模板 (onboarding template) — provisioning defaults applied to
    # every newly created profile (SOUL / Personality / skills / model).
    # Stored as a single JSON file; see hermes_cloud.app.onboarding_template.
    # ------------------------------------------------------------------
    from hermes_cloud.app import onboarding_template as _ob_tpl

    def _hermes_home_path() -> Path:
        return Path(cfg.hermes_home_root) if cfg.hermes_home_root else Path.home() / ".hermes"

    def _snapshot_profile_as_template(profile_id: str, user_id: str) -> dict:
        """Build a template dict from a profile's current config (the seed)."""
        pdir = _profile_dir(profile_id)
        soul = ""
        soul_path = pdir / "SOUL.md"
        if soul_path.is_file():
            try:
                soul = soul_path.read_text(encoding="utf-8")
            except Exception:
                soul = ""
        data = _read_profile_config(profile_id)
        system_prompt = ""
        agent = data.get("agent")
        if isinstance(agent, dict):
            system_prompt = agent.get("system_prompt") or ""
        disabled = sorted(_read_profile_disabled(pdir / "config.yaml"))
        try:
            model_cfg = app.state.config_store.get_user_model(str(user_id))
        except Exception:
            model_cfg = None
        model = {
            "model": model_cfg.model if model_cfg else None,
            "api_key": model_cfg.api_key if model_cfg else None,
            "base_url": model_cfg.base_url if model_cfg else None,
            "providers_json": model_cfg.providers_json if model_cfg else None,
        }
        return {
            "soul_md": soul,
            "system_prompt": system_prompt,
            "skills_disabled": disabled,
            # First-time seed leaves cron_jobs empty on purpose — admin
            # must click 「从 admin 复制定时任务」 to opt in, matching the
            # "template is independent from any profile" rule.
            "cron_jobs": [],
            "model": model,
        }

    def _apply_onboarding_template(profile_id: str, user_id: str) -> bool:
        """Apply the stored 新用户模板 to a profile.

        Returns False (no-op) when no template has been configured yet.
        Model config is written through config_store + _sync_config_to_profile
        so it survives later sync calls; SOUL.md / Personality / skills land
        directly in the profile's files. Takes effect on the profile's next
        conversation (config.yaml is re-read per request; SOUL.md per session).
        """
        tpl = _ob_tpl.read_template(_hermes_home_path())
        if tpl is None:
            return False
        pdir = _profile_dir(profile_id)
        pdir.mkdir(parents=True, exist_ok=True)
        # SOUL.md
        soul = tpl.get("soul_md") or ""
        if soul.strip():
            (pdir / "SOUL.md").write_text(soul, encoding="utf-8")
        # Model — seed config_store (default profile) then sync to config.yaml.
        model = tpl.get("model") or {}
        if any(model.get(k) for k in ("model", "api_key", "base_url", "providers_json")):
            try:
                app.state.config_store.set_user_model(
                    user_id=str(user_id),
                    model=model.get("model"),
                    api_key=model.get("api_key"),
                    base_url=model.get("base_url"),
                    providers_json=model.get("providers_json"),
                )
                _sync_config_to_profile(str(user_id), profile_id)
            except Exception as exc:
                logger.warning(
                    "apply_onboarding_template: model sync failed pid=%s: %s",
                    profile_id, exc,
                )
        # Personality + skills.disabled → config.yaml (read what _sync wrote,
        # merge agent + skills, write back — the model section is preserved).
        data = _read_profile_config(profile_id)
        agent = data.get("agent")
        if not isinstance(agent, dict):
            agent = {}
        agent["system_prompt"] = tpl.get("system_prompt") or ""
        data["agent"] = agent
        skills_cfg = data.get("skills")
        if not isinstance(skills_cfg, dict):
            skills_cfg = {}
        skills_cfg["disabled"] = sorted({str(x) for x in (tpl.get("skills_disabled") or [])})
        data["skills"] = skills_cfg
        _write_profile_config(profile_id, data)
        # Cron jobs: write the template's snapshot list (could be empty)
        # to the profile's jobs.json. Each job gets a fresh uuid +
        # provisioned_by="onboarding_template" so the legacy
        # cron_provisioner's drift-reconcile leaves them alone.
        _apply_template_cron_jobs(pdir, tpl.get("cron_jobs") or [])
        return True

    def _apply_template_cron_jobs(pdir: Path, jobs: list[dict]) -> None:
        """Write the template's cron job list to ``<profile>/cron/jobs.json``.

        Drops any pre-existing template-provisioned rows (to honor
        admin's edits in the template — including row deletions) but
        preserves jobs the user created themselves or that the legacy
        cron_provisioner owns. New rows get a fresh uuid each apply so
        re-applying doesn't collide with a previous run.
        """
        import json as _json
        import uuid as _uuid
        cron_dir = pdir / "cron"
        cron_dir.mkdir(parents=True, exist_ok=True)
        jobs_path = cron_dir / "jobs.json"
        existing: dict = {"jobs": []}
        if jobs_path.is_file():
            try:
                existing = _json.loads(jobs_path.read_text(encoding="utf-8"))
                if not isinstance(existing, dict) or "jobs" not in existing:
                    existing = {"jobs": []}
            except Exception:
                existing = {"jobs": []}
        # Strip prior onboarding-template rows so deletions in the
        # template propagate on re-apply.
        kept = [
            j for j in existing.get("jobs", [])
            if not (isinstance(j, dict)
                    and j.get("provisioned_by") == "onboarding_template")
        ]
        # Mint fresh rows from template; copy spec fields, ignore
        # runtime fields like last_run_at/next_run_at (scheduler fills).
        from datetime import datetime as _dt, timezone as _tz
        now_iso = _dt.now(_tz.utc).isoformat()
        for spec in jobs:
            row = dict(spec)
            row["id"] = _uuid.uuid4().hex[:12]
            row["provisioned_by"] = "onboarding_template"
            row["created_at"] = now_iso
            # Clear scheduler runtime state so the new profile starts fresh.
            for k in ("next_run_at", "last_run_at", "last_status",
                      "last_error", "last_delivery_error"):
                row[k] = None
            repeat = row.get("repeat")
            if isinstance(repeat, dict):
                repeat = dict(repeat)
                repeat["completed"] = 0
                row["repeat"] = repeat
            kept.append(row)
        existing["jobs"] = kept
        existing["updated_at"] = now_iso
        jobs_path.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @app.get("/api/admin/onboarding-template")
    def get_onboarding_template(request: Request):
        admin_id = require_admin_auth(request, cfg)
        home = _hermes_home_path()
        tpl = _ob_tpl.read_template(home)
        if tpl is None:
            # First use — seed from the requesting admin's own profile.
            seed_profile = _resolve_profile_id(request, admin_id) or admin_id
            tpl = _snapshot_profile_as_template(seed_profile, str(admin_id))
            _ob_tpl.write_template(home, tpl)
            tpl = _ob_tpl.read_template(home) or tpl
        model = dict(tpl.get("model") or {})
        model["api_key"] = _ob_tpl.mask_secret(model.get("api_key"))
        return {
            "soul_md": tpl.get("soul_md") or "",
            "system_prompt": tpl.get("system_prompt") or "",
            "skills_disabled": tpl.get("skills_disabled") or [],
            "cron_jobs": tpl.get("cron_jobs") or [],
            "model": model,
        }

    @app.put("/api/admin/onboarding-template")
    async def put_onboarding_template(request: Request):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        home = _hermes_home_path()
        current = _ob_tpl.read_template(home) or _ob_tpl.empty_template()
        cur_model = current.get("model") or {}
        in_model = body.get("model")
        in_model = in_model if isinstance(in_model, dict) else {}
        # A masked api_key echoed back means "unchanged" — keep the stored one.
        in_key = in_model.get("api_key")
        if in_key is None or _ob_tpl.is_masked(in_key):
            new_key = cur_model.get("api_key")
        else:
            new_key = in_key
        new = {
            "soul_md": str(body.get("soul_md", current.get("soul_md") or "")),
            "system_prompt": str(body.get("system_prompt", current.get("system_prompt") or "")),
            "skills_disabled": body.get("skills_disabled", current.get("skills_disabled") or []),
            "cron_jobs": body.get("cron_jobs", current.get("cron_jobs") or []),
            "model": {
                "model": in_model.get("model", cur_model.get("model")),
                "api_key": new_key,
                "base_url": in_model.get("base_url", cur_model.get("base_url")),
                "providers_json": in_model.get("providers_json", cur_model.get("providers_json")),
            },
        }
        _ob_tpl.write_template(home, new)
        return {"ok": True}

    @app.post("/api/admin/onboarding-template/apply/{user_id}")
    async def apply_onboarding_template_to_user(user_id: str, request: Request):
        require_admin_auth(request, cfg)
        try:
            profile = app.state.profile_store.ensure_default_profile(user_id=user_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": f"无法定位该用户的 profile: {exc}",
                                  "type": "not_found_error", "code": "profile_not_found"}},
            )
        applied = _apply_onboarding_template(profile.id, str(user_id))
        if not applied:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "新用户模板尚未设置。",
                                  "type": "invalid_request_error", "code": "no_template"}},
            )
        return {"ok": True, "user_id": user_id, "profile_id": profile.id,
                "note": "已应用模板，将在该用户下一次新对话生效。"}

    @app.get("/api/admin/onboarding-template/bundled-skills")
    def get_template_bundled_skills(request: Request):
        """List the skills hermes will auto-copy to every new profile.

        Reads ``hermes/skills/`` (the actually-bundled set) — NOT any
        per-profile skills/ dir. That distinction matters: admin's
        own profile can accumulate hub-installed or hand-added skills
        that won't propagate, so the template editor must show what
        new users will *actually* inherit, not admin's superset.
        """
        require_admin_auth(request, cfg)
        bundled_dir = (
            Path(_os.environ.get("HERMES_BUNDLED_SKILLS"))
            if _os.environ.get("HERMES_BUNDLED_SKILLS")
            else Path(__file__).resolve().parents[2] / "hermes" / "skills"
        )
        return {"skills": _scan_profile_skills(bundled_dir)}

    @app.get("/api/admin/onboarding-template/cron-snapshot")
    def get_template_cron_snapshot(request: Request):
        """Read the requesting admin's current ``cron/jobs.json``.

        Used by the SPA's 「从 admin 复制定时任务」 button to seed the
        template's cron_jobs list. Pure read — does not write the
        template. Admin reviews/edits in the UI, then 保存模板 to commit.
        """
        admin_id = require_admin_auth(request, cfg)
        admin_profile = _resolve_profile_id(request, admin_id) or admin_id
        jobs_path = _profile_dir(admin_profile) / "cron" / "jobs.json"
        if not jobs_path.is_file():
            return {"jobs": []}
        try:
            import json as _json
            doc = _json.loads(jobs_path.read_text(encoding="utf-8"))
        except Exception:
            return {"jobs": []}
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        if not isinstance(jobs, list):
            return {"jobs": []}
        # Strip runtime/identity fields that shouldn't follow into the
        # template — id, scheduler state, completed counter, owning
        # user_id. What we keep is the spec: name, schedule, skill,
        # prompt, deliver, enabled, deliver_render.
        cleaned: list[dict] = []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            spec = {
                k: j.get(k) for k in (
                    "name", "prompt", "skill", "skills", "schedule",
                    "schedule_display", "deliver", "deliver_render",
                    "enabled", "model", "provider", "base_url", "script",
                    "no_agent", "context_from", "enabled_toolsets",
                )
                if k in j
            }
            cleaned.append(spec)
        return {"jobs": cleaned}

    # ------------------------------------------------------------------
    # Channel credentials — per-profile bot setup.
    #
    # Each channel's config lives in the profile's config.yaml under
    # ``platforms.<channel>.extra``. Hermes reads YAML on every request,
    # so changes here take effect on the next inbound message — no DB,
    # no encryption, no sync layer. RBAC: profile owner OR is_admin.
    # ------------------------------------------------------------------
    def _require_profile_owner_or_admin(
        request: Request, profile_id: str,
    ) -> str:
        """Return the authenticated user_id if they own ``profile_id`` or
        are an admin. Raises 401/403 otherwise.

        Owner check uses the profile_store's ``profile.user_id`` field;
        admin check uses ``is_admin`` on the user account (same gate
        as :func:`require_admin_auth`).
        """
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        store = app.state.profile_store
        profile = store.get_profile(profile_id) if hasattr(store, "get_profile") else None
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {
                    "message": f"Profile not found: {profile_id}",
                    "type": "not_found", "code": "profile_not_found",
                }},
            )
        if str(profile.user_id) == str(user_id):
            return str(user_id)
        # Owner check failed — try admin.
        auth_store = getattr(app.state, "auth_store", None)
        if auth_store is not None and hasattr(auth_store, "get_user"):
            user = auth_store.get_user(user_id)
            if user is not None and getattr(user, "is_admin", False):
                return str(user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {
                "message": "Profile owner or admin role required.",
                "type": "permission_error", "code": "forbidden",
            }},
        )

    async def _restart_profile_best_effort(profile_id: str) -> None:
        """Restart a profile's subprocess so hermes re-reads config.yaml
        and (re)wires channel adapters. Best-effort: if the profile
        isn't running, the next inbound message spawns it fresh with
        the current config — so a restart failure is never fatal.

        Resolves the cloud user_id from profile_store and passes it
        through — the subprocess needs an integer CRM_USER_ID or
        crm_tools registers nothing.
        """
        pm = app.state.profile_manager
        uid: str | None = None
        try:
            store = getattr(app.state, "profile_store", None)
            prof = store.get_profile(profile_id) if store is not None else None
            if prof is not None:
                uid = str(prof.user_id)
        except Exception:
            pass
        try:
            if hasattr(pm, "restart_profile"):
                await pm.restart_profile(profile_id, user_id=uid)
            else:
                await pm.stop_profile(profile_id)
        except Exception:
            logger.warning(
                "channel config: profile restart failed for %s "
                "(non-fatal — next inbound respawns it)", profile_id,
                exc_info=True,
            )

    @app.get("/api/profiles/{profile_id}/channels")
    def list_profile_channels(profile_id: str, request: Request):
        """List channels with their configured/unconfigured status.

        Each entry: ``{channel, display_name, supports_qr, configured,
        bot_name, status}``. Used by the SPA Channels tab to render the
        whole list with status pills at a glance.
        """
        from hermes_cloud.app.channel_registry import CHANNEL_REGISTRY

        _require_profile_owner_or_admin(request, profile_id)
        out = []
        for name, spec in CHANNEL_REGISTRY.items():
            extra = _read_channel_config(profile_id, name)
            out.append({
                "channel": name,
                "display_name": spec.display_name,
                "supports_qr": spec.supports_qr,
                "supports_probe": spec.test_fn is not None,
                "configured": bool(extra),
                "bot_name": extra.get("bot_name", ""),
                "required_fields": spec.required_fields,
                "optional_fields": spec.optional_fields,
                "secret_fields": spec.secret_fields,
            })
        return {"channels": out}

    @app.get("/api/profiles/{profile_id}/channels/{channel}")
    def get_profile_channel(profile_id: str, channel: str, request: Request):
        """Return one channel's config with secret fields masked.

        404 if the channel name is unknown to the registry; empty
        ``extra`` if known but not configured for this profile.
        """
        from hermes_cloud.app.channel_registry import (
            CHANNEL_REGISTRY, get_spec,
        )

        _require_profile_owner_or_admin(request, profile_id)
        if channel not in CHANNEL_REGISTRY:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {
                    "message": f"Unknown channel: {channel}",
                    "type": "not_found", "code": "unknown_channel",
                }},
            )
        spec = get_spec(channel)
        extra = _read_channel_config(profile_id, channel)
        return {
            "channel": channel,
            "display_name": spec.display_name,
            "configured": bool(extra),
            "supports_qr": spec.supports_qr,
            "supports_probe": spec.test_fn is not None,
            "required_fields": spec.required_fields,
            "optional_fields": spec.optional_fields,
            "secret_fields": spec.secret_fields,
            "extra": spec.mask_secrets(extra),
        }

    @app.put("/api/profiles/{profile_id}/channels/{channel}")
    async def put_profile_channel(
        profile_id: str, channel: str, request: Request,
    ):
        """Upsert one channel's config. Body is the ``extra`` dict.

        Behavior:
        - Validates against ``ChannelSpec.validate`` → 400 on bad shape
        - Merges with existing config: fields the caller doesn't send
          stay as-is (so the masked secret round-trip from GET works —
          re-PUTting the masked GET response doesn't wipe the secret).
        - Restarts the subprocess so hermes re-wires channel
          adapters with the new credentials.
        """
        from hermes_cloud.app.channel_registry import (
            CHANNEL_REGISTRY, ChannelConfigError, get_spec,
        )

        _require_profile_owner_or_admin(request, profile_id)
        if channel not in CHANNEL_REGISTRY:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {
                    "message": f"Unknown channel: {channel}",
                    "type": "not_found", "code": "unknown_channel",
                }},
            )
        spec = get_spec(channel)
        body = await _parse_json_body(request)
        incoming = body if isinstance(body, dict) else {}

        # Merge: caller may send a partial update; preserve unsent
        # fields so masked-GET → PUT round-trips work without the user
        # having to retype secrets.
        existing = _read_channel_config(profile_id, channel)
        merged = dict(existing)
        for k, v in incoming.items():
            # Skip masked values — if the SPA sent back the ●●●●●●xxx
            # placeholder, treat that as "leave this field as-is".
            if isinstance(v, str) and v.startswith("●●●●"):
                continue
            merged[k] = v

        try:
            spec.validate(merged)
        except ChannelConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "invalid_channel_config",
                }},
            )

        _write_channel_config(profile_id, channel, merged)
        # Restart the subprocess so hermes' native adapter picks up the
        # new channel credentials. Adapters are wired at gateway boot —
        # a running subprocess won't connect a newly-configured bot
        # until it restarts. Best-effort: a not-yet-running profile is
        # spawned fresh on its first inbound, which reads the new config.
        await _restart_profile_best_effort(profile_id)
        # Provision default cron jobs (daily-coach / weekly-digest / ...)
        # now that this profile has at least one active channel. The
        # boot-time ``backfill_all_profiles`` would otherwise skip a
        # channel-less profile and never re-check until the next restart,
        # so users who bind a channel post-boot stayed cron-less. This
        # call is idempotent and best-effort.
        try:
            from hermes_cloud.app.cron_provisioner import ensure_default_jobs
            ensure_default_jobs(profile_id, _hermes_home_path())
        except Exception as exc:
            logger.warning(
                "channel PUT: cron provision failed profile=%s: %s",
                profile_id, exc,
            )
        return {
            "channel": channel,
            "configured": True,
            "extra": spec.mask_secrets(merged),
        }

    @app.delete("/api/profiles/{profile_id}/channels/{channel}")
    async def delete_profile_channel(
        profile_id: str, channel: str, request: Request,
    ):
        """Disconnect a channel by removing its entry from config.yaml.

        Restarts the subprocess so the (now-absent) platform entry
        means hermes' ``GatewayConfig.from_dict`` skips registering
        the adapter — the bot's WebSocket drops cleanly.
        """
        from hermes_cloud.app.channel_registry import CHANNEL_REGISTRY

        _require_profile_owner_or_admin(request, profile_id)
        if channel not in CHANNEL_REGISTRY:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {
                    "message": f"Unknown channel: {channel}",
                    "type": "not_found", "code": "unknown_channel",
                }},
            )
        _write_channel_config(profile_id, channel, {})
        await _restart_profile_best_effort(profile_id)
        # Reconcile cron: ``ensure_default_jobs`` removes provisioned
        # jobs when a profile loses all channels (no one would receive
        # the push anyway). Idempotent + best-effort.
        try:
            from hermes_cloud.app.cron_provisioner import ensure_default_jobs
            ensure_default_jobs(profile_id, _hermes_home_path())
        except Exception as exc:
            logger.warning(
                "channel DELETE: cron reconcile failed profile=%s: %s",
                profile_id, exc,
            )
        return {"channel": channel, "configured": False}

    @app.post("/api/profiles/{profile_id}/channels/{channel}/test")
    async def test_profile_channel(
        profile_id: str, channel: str, request: Request,
    ):
        """Probe the stored creds — open a temp connection and report
        whether the credentials work. Surfaces errors inline so users
        see actionable feedback ("wrong app_secret") instead of "the
        bot doesn't reply".
        """
        from hermes_cloud.app.channel_registry import (
            CHANNEL_REGISTRY, get_spec,
        )

        _require_profile_owner_or_admin(request, profile_id)
        if channel not in CHANNEL_REGISTRY:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {
                    "message": f"Unknown channel: {channel}",
                    "type": "not_found", "code": "unknown_channel",
                }},
            )
        spec = get_spec(channel)
        extra = _read_channel_config(profile_id, channel)
        if not extra:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": "Channel not configured. PUT credentials first.",
                    "type": "invalid_request_error",
                    "code": "not_configured",
                }},
            )
        if spec.test_fn is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={"error": {
                    "message": f"Probe not implemented for channel={channel}",
                    "type": "server_error",
                    "code": "probe_unsupported",
                }},
            )
        try:
            result = await spec.test_fn(extra, _profile_dir(profile_id))
        except Exception as exc:
            return {
                "ok": False,
                "error": f"probe raised: {exc}",
                "latency_ms": 0,
                "bot_name": "",
            }
        return {
            "ok": result.ok,
            "error": result.error,
            "latency_ms": result.latency_ms,
            "bot_name": result.bot_name,
        }

    # ------------------------------------------------------------------
    # QR onboarding — Feishu / WeChat scan-to-connect via hermes'
    # native qr_start / qr_poll_once primitives. The SPA POSTs /start,
    # gets a QR URL + session_id, displays the QR, and polls /poll
    # every few seconds until completed / expired / denied.
    # ------------------------------------------------------------------

    # In-memory session table — process-local because QR flows are
    # short-lived (<10 min) and tied to a specific browser instance.
    # Survives a SPA reload but not a cloud restart (user just clicks
    # "扫码" again to start a fresh session).
    if not hasattr(app.state, "qr_sessions"):
        app.state.qr_sessions = {}  # session_id → dict

    def _gen_qr_session_id() -> str:
        import secrets
        return secrets.token_urlsafe(16)

    @app.post("/api/profiles/{profile_id}/channels/{channel}/qr/start")
    async def qr_start_endpoint(
        profile_id: str, channel: str, request: Request,
    ):
        """Begin device-code flow. Returns {session_id, qr_url, expires_at}.

        ``session_id`` is server-issued; the SPA echoes it back on every
        /qr/poll. ``qr_url`` is what the SPA renders as a QR code image.
        Supported QR channels: feishu, weixin, dingtalk, wecom; others 400.
        """
        from hermes_cloud.app.channel_registry import CHANNEL_REGISTRY

        _require_profile_owner_or_admin(request, profile_id)
        if channel not in CHANNEL_REGISTRY:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": f"Unknown channel: {channel}",
                                  "code": "unknown_channel"}},
            )
        spec = CHANNEL_REGISTRY[channel]
        if not spec.supports_qr:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": f"Channel '{channel}' does not support QR onboarding. "
                               f"Use the form-paste flow.",
                    "code": "qr_not_supported",
                }},
            )
        import time as _time
        session_id = _gen_qr_session_id()

        if channel == "feishu":
            from plugins.platforms.feishu.adapter import qr_start as _feishu_qr_start
            body = await _parse_json_body(request) if (
                request.headers.get("content-length", "0") != "0"
            ) else {}
            domain = (body.get("domain") or "feishu").strip().lower()
            if domain not in ("feishu", "lark"):
                domain = "feishu"
            try:
                started = _feishu_qr_start(domain=domain)
            except Exception as exc:
                logger.exception("feishu qr_start failed")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={"error": {"message": f"Feishu QR setup failed: {exc}",
                                      "code": "qr_start_failed"}},
                )
            expires_at = _time.time() + started["expire_in"]
            app.state.qr_sessions[session_id] = {
                "channel": "feishu",
                "profile_id": profile_id,
                "device_code": started["device_code"],
                "domain": started["domain"],
                "expires_at": expires_at,
                "interval": started["interval"],
            }
            return {
                "session_id": session_id,
                "qr_url": started["qr_url"],
                "user_code": started["user_code"],
                "interval": started["interval"],
                "expires_at": int(expires_at),
            }

        if channel == "weixin":
            from gateway.platforms.weixin import qr_start_async as _weixin_qr_start
            try:
                started = await _weixin_qr_start()
            except Exception as exc:
                logger.exception("weixin qr_start failed")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={"error": {"message": f"WeChat QR setup failed: {exc}",
                                      "code": "qr_start_failed"}},
                )
            expires_at = _time.time() + started["expire_in"]
            app.state.qr_sessions[session_id] = {
                "channel": "weixin",
                "profile_id": profile_id,
                "qrcode": started["qrcode"],
                "base_url": started["base_url"],
                "expires_at": expires_at,
                "interval": 2,
            }
            return {
                "session_id": session_id,
                "qr_url": started["qrcode_url"] or started["qrcode"],
                "interval": 2,
                "expires_at": int(expires_at),
            }

        if channel == "dingtalk":
            from hermes_cli.dingtalk_auth import begin_registration
            try:
                reg = begin_registration()
            except Exception as exc:
                logger.exception("dingtalk qr begin failed")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={"error": {"message": f"钉钉扫码初始化失败: {exc}",
                                      "code": "qr_start_failed"}},
                )
            expires_at = _time.time() + reg["expires_in"]
            app.state.qr_sessions[session_id] = {
                "channel": "dingtalk",
                "profile_id": profile_id,
                "device_code": reg["device_code"],
                "expires_at": expires_at,
                "interval": reg["interval"],
            }
            return {
                "session_id": session_id,
                "qr_url": reg["verification_uri_complete"],
                "interval": reg["interval"],
                "expires_at": int(expires_at),
            }

        if channel == "wecom":
            from hermes_cloud.app.qr.wecom_qr import qr_start as _wecom_qr_start
            try:
                started = await _wecom_qr_start()
            except Exception as exc:
                logger.exception("wecom qr start failed")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={"error": {"message": f"企业微信扫码初始化失败: {exc}",
                                      "code": "qr_start_failed"}},
                )
            expires_at = _time.time() + started["expires_in"]
            app.state.qr_sessions[session_id] = {
                "channel": "wecom",
                "profile_id": profile_id,
                "scode": started["scode"],
                "expires_at": expires_at,
                "interval": 3,
            }
            return {
                "session_id": session_id,
                "qr_url": started["qr_url"],
                "interval": 3,
                "expires_at": int(expires_at),
            }

        # Defensive — spec.supports_qr lied
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": {"message": f"QR start not wired for {channel}",
                              "code": "qr_not_implemented"}},
        )

    @app.get("/api/profiles/{profile_id}/channels/{channel}/qr/poll")
    async def qr_poll_endpoint(
        profile_id: str, channel: str, session_id: str, request: Request,
    ):
        """Poll one tick. Returns status discriminated union.

        On ``completed`` also writes the creds into profile config.yaml
        (so hermes' next request picks up the adapter) and returns the
        masked config so SPA can immediately switch to "Connected"
        state without a follow-up GET.
        """
        import time as _time
        from hermes_cloud.app.channel_registry import CHANNEL_REGISTRY, get_spec

        _require_profile_owner_or_admin(request, profile_id)
        sess = app.state.qr_sessions.get(session_id)
        if not sess:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "Unknown or expired session_id",
                                  "code": "qr_session_not_found"}},
            )
        if sess["channel"] != channel or sess["profile_id"] != profile_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"message": "session_id does not match channel / profile",
                                  "code": "qr_session_mismatch"}},
            )
        # Terminal-state replay: once a session hits completed/expired/denied
        # we keep the entry around for a grace window (~60s, see
        # ``terminal_until``) so racing polls from the SPA's setInterval
        # don't get misleading 404s while ``clearTimers()`` propagates.
        if sess.get("terminal_status"):
            if _time.time() > sess.get("terminal_until", 0):
                app.state.qr_sessions.pop(session_id, None)
                return {"status": sess["terminal_status"]}
            return sess.get("terminal_response") or {"status": sess["terminal_status"]}
        if _time.time() > sess["expires_at"]:
            app.state.qr_sessions.pop(session_id, None)
            return {"status": "expired"}

        if channel == "feishu":
            from plugins.platforms.feishu.adapter import qr_poll_once as _feishu_poll
            result = _feishu_poll(sess["device_code"], domain=sess["domain"])
            # Update domain if Lark auto-detected mid-flow
            if result.get("domain") and result["domain"] != sess["domain"]:
                sess["domain"] = result["domain"]
        elif channel == "weixin":
            from gateway.platforms.weixin import qr_poll_once_async as _weixin_poll
            result = await _weixin_poll(
                sess["qrcode"], base_url=sess["base_url"],
                hermes_home=None,  # cloud writes via config.yaml, not env
            )
            # iLink load-balancer redirect: persist new base_url for
            # subsequent polls within this session.
            if result.get("status") == "redirect":
                sess["base_url"] = result["base_url"]
                return {"status": "pending"}
        elif channel == "dingtalk":
            from hermes_cli.dingtalk_auth import poll_registration, RegistrationError
            # poll_registration is a blocking (requests) call with a 15s
            # connect timeout — run it off the event loop so a slow / timing-
            # out DingTalk poll doesn't freeze the whole server. Transient
            # network / API errors (RegistrationError) are swallowed as
            # "pending" so one blip doesn't abort the scan; the next poll
            # retries until the user authorizes or the session expires.
            try:
                reg = await asyncio.to_thread(poll_registration, sess["device_code"])
            except RegistrationError as exc:
                logger.debug("dingtalk qr poll transient error: %s", exc)
                result = {"status": "pending"}
            else:
                st = reg["status"]
                if st == "SUCCESS":
                    result = {"status": "completed",
                              "creds": {"client_id": reg["client_id"],
                                        "client_secret": reg["client_secret"]}}
                elif st == "WAITING":
                    result = {"status": "pending"}
                elif st == "EXPIRED":
                    result = {"status": "expired"}
                else:  # FAIL / UNKNOWN
                    result = {"status": "denied"}
        elif channel == "wecom":
            from hermes_cloud.app.qr.wecom_qr import qr_poll_once as _wecom_poll
            result = await _wecom_poll(sess["scode"])
        else:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={"error": {"message": f"QR poll not wired for {channel}",
                                  "code": "qr_not_implemented"}},
            )

        # Transient provider-side poll blips (e.g. Feishu/WeChat returning
        # "transport: The read operation timed out") must NOT abort the
        # scan — flaky connectivity to the IM cloud is common. Treat a
        # clearly-transient error as ``pending`` so the SPA keeps polling
        # until success or the session's own expiry. Mirrors the dingtalk
        # poll resilience; genuinely terminal errors (denied/invalid) carry
        # different messages and fall through unchanged.
        if result.get("status") == "error":
            _emsg = str(result.get("message", "")).lower()
            if any(kw in _emsg for kw in (
                "timed out", "timeout", "transport", "read operation",
                "temporarily", "connection", "reset",
            )):
                logger.debug("qr poll transient error (%s), keep polling: %s",
                             channel, result.get("message"))
                result = {"status": "pending"}

        if result["status"] == "completed":
            creds = result["creds"]
            # Drop fields the hermes adapter doesn't expect in extra
            # (open_id of the scanning user is metadata, not config).
            extra: dict = {}
            if channel == "feishu":
                extra = {
                    "app_id": creds["app_id"],
                    "app_secret": creds["app_secret"],
                    "domain": creds["domain"],
                    "connection_mode": "websocket",
                }
                if creds.get("bot_name"):
                    extra["bot_name"] = creds["bot_name"]
                if creds.get("bot_open_id"):
                    extra["bot_open_id"] = creds["bot_open_id"]
            elif channel == "weixin":
                extra = {
                    "account_id": creds["account_id"],
                    "token": creds["token"],
                    "base_url": creds["base_url"],
                }
                if creds.get("user_id"):
                    extra["user_id"] = creds["user_id"]
            elif channel == "dingtalk":
                extra = {
                    "client_id": creds["client_id"],
                    "client_secret": creds["client_secret"],
                }
            elif channel == "wecom":
                extra = {
                    "bot_id": creds["bot_id"],
                    "secret": creds["secret"],
                }
            _write_channel_config(profile_id, channel, extra)
            # Per-profile-bot model: record the bot owner's own channel
            # id as the home_channel (cron-delivery target) in the
            # profile's config.yaml, so its hermes subprocess delivers
            # proactive pushes itself — no channel_bindings, no HOME_* env.
            owner_chat_id = ""
            if channel == "feishu":
                owner_chat_id = str(creds.get("open_id") or "")
            elif channel == "weixin":
                owner_chat_id = str(creds.get("user_id") or "")
            if owner_chat_id:
                from hermes_cloud.app.channel_config import set_home_channel
                set_home_channel(_profile_dir(profile_id), channel, owner_chat_id)
            await _restart_profile_best_effort(profile_id)
            spec = get_spec(channel)
            response = {
                "status": "completed",
                "extra": spec.mask_secrets(extra),
            }
            sess["terminal_status"] = "completed"
            sess["terminal_response"] = response
            sess["terminal_until"] = _time.time() + 60
            return response

        if result["status"] in ("expired", "denied"):
            sess["terminal_status"] = result["status"]
            sess["terminal_response"] = {"status": result["status"]}
            sess["terminal_until"] = _time.time() + 60
            return {"status": result["status"]}

        if result["status"] == "error":
            return {
                "status": "error",
                "message": result.get("message", "unknown error"),
            }

        # pending / scanned — keep polling
        return {"status": result["status"]}

    # ------------------------------------------------------------------
    # SOUL.md — per-profile free-form identity overlay
    # ------------------------------------------------------------------
    @app.get("/api/user-config/soul")
    def get_user_soul(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id) or user_id
        soul_path = _profile_dir(profile_id) / "SOUL.md"
        content = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
        return {"soul": content}

    @app.put("/api/user-config/soul")
    async def put_user_soul(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id) or user_id
        body = await _parse_json_body(request)
        content = body.get("soul", "")
        if not isinstance(content, str):
            raise HTTPException(
                status_code=400,
                detail={"error": {"message": "soul must be a string",
                                  "type": "invalid_request_error",
                                  "code": "invalid_field"}},
            )
        soul_dir = _profile_dir(profile_id)
        soul_dir.mkdir(parents=True, exist_ok=True)
        (soul_dir / "SOUL.md").write_text(content, encoding="utf-8")
        return {"soul": content}

    # ------------------------------------------------------------------
    # Workspace files — generic Markdown editor surface for per-profile
    # context files (SOUL.md, memories/MEMORY.md, memories/USER.md,
    # plus any other .md hermes drops into the profile dir or memories/).
    # Files only appear if they exist on disk — we don't invent empty
    # placeholders for IDENTITY/AGENTS/TOOLS/HEARTBEAT unless hermes
    # actually creates them.
    # ------------------------------------------------------------------
    _WORKSPACE_ALWAYS = (
        # (relative path, display name). Always surfaced so the user can
        # edit/create them even on a fresh profile.
        ("SOUL.md", "SOUL.md"),
        ("memories/MEMORY.md", "MEMORY.md"),
        ("memories/USER.md", "USER.md"),
    )
    _WORKSPACE_WRITE_DIRS = ("", "memories")  # relative to profile dir

    def _workspace_list_files(profile_id: str) -> list[dict]:
        pdir = _profile_dir(profile_id)
        seen: set[str] = set()
        out: list[dict] = []

        def _add(rel: str, display: str) -> None:
            if rel in seen:
                return
            seen.add(rel)
            p = pdir / rel
            content = p.read_text(encoding="utf-8") if p.exists() else ""
            out.append({"name": display, "path": rel, "content": content})

        for rel, display in _WORKSPACE_ALWAYS:
            _add(rel, display)

        # Auto-discover any other .md files in the profile root or in
        # memories/. Everything else (logs/, sessions/, …) is off-limits.
        if pdir.exists():
            for p in sorted(pdir.glob("*.md")):
                _add(p.name, p.name)
            mem = pdir / "memories"
            if mem.exists():
                for p in sorted(mem.glob("*.md")):
                    _add(f"memories/{p.name}", p.name)
        return out

    def _workspace_validate_path(rel: str) -> Path | None:
        """Reject path traversal + anything outside the allowed subtree."""
        rel = (rel or "").strip().lstrip("/")
        if not rel or ".." in rel.split("/"):
            return None
        if not rel.endswith(".md"):
            return None
        parts = rel.split("/")
        if len(parts) == 1:
            return Path(rel)
        if len(parts) == 2 and parts[0] in _WORKSPACE_WRITE_DIRS and parts[0]:
            return Path(rel)
        return None

    @app.get("/api/workspace/files")
    def get_workspace_files(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id) or user_id
        return JSONResponse({"files": _workspace_list_files(profile_id)})

    @app.put("/api/workspace/files")
    async def put_workspace_file(request: Request):
        user_id = require_bearer_auth(request, cfg) or "local-dev"
        profile_id = _resolve_profile_id(request, user_id) or user_id
        body = await _parse_json_body(request)
        rel_raw = body.get("path", "")
        content = body.get("content", "")
        if not isinstance(content, str):
            raise HTTPException(
                status_code=400,
                detail={"error": {"message": "content must be a string",
                                  "type": "invalid_request_error",
                                  "code": "invalid_field"}},
            )
        safe_rel = _workspace_validate_path(str(rel_raw))
        if safe_rel is None:
            raise HTTPException(
                status_code=400,
                detail={"error": {"message": f"Invalid workspace path: {rel_raw!r}",
                                  "type": "invalid_request_error",
                                  "code": "invalid_path"}},
            )
        pdir = _profile_dir(profile_id)
        target = pdir / safe_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(safe_rel), "content": content}

    # ------------------------------------------------------------------
    # Scheduling — proxy /api/jobs/* to the user's hermes process
    # ------------------------------------------------------------------
    def _cron_default_deliver(request: Request, user_id: str, raw_body: bytes) -> bytes:
        """SPA-created cron jobs carry no ``deliver``, so hermes core
        defaults them to ``local`` (web timeline only) and the user never
        gets a WeChat/Feishu push. Inject ``deliver`` = every channel the
        profile has configured, so scheduled tasks reach the IM channels
        the user actually connected. An explicit ``deliver`` is left
        untouched; no configured channels → leave hermes' ``local`` default.
        """
        from hermes_cloud.app.channel_registry import CHANNEL_REGISTRY
        try:
            body = json.loads(raw_body) if raw_body else {}
        except (ValueError, TypeError):
            return raw_body
        if not isinstance(body, dict) or body.get("deliver"):
            return raw_body
        profile_id = _resolve_profile_id(request, user_id) or user_id
        channels = [
            name for name in CHANNEL_REGISTRY
            if _read_channel_config(profile_id, name)
        ]
        if not channels:
            return raw_body
        body["deliver"] = ",".join(channels)
        return json.dumps(body).encode("utf-8")

    @app.api_route("/api/jobs", methods=["GET", "POST"])
    async def proxy_jobs(request: Request):
        user_id = require_bearer_auth(request, cfg)
        raw_body = await request.body()
        if request.method == "POST":
            raw_body = _cron_default_deliver(request, user_id, raw_body)
        return await _proxy(request, user_id, "/api/jobs", method=request.method, raw_body=raw_body)

    @app.api_route("/api/jobs/{job_id}", methods=["GET", "PATCH", "DELETE"])
    async def proxy_job(request: Request, job_id: str):
        user_id = require_bearer_auth(request, cfg)
        raw_body = await request.body()
        return await _proxy(request, user_id, f"/api/jobs/{job_id}", method=request.method, raw_body=raw_body)

    @app.post("/api/jobs/{job_id}/pause")
    async def proxy_job_pause(request: Request, job_id: str):
        user_id = require_bearer_auth(request, cfg)
        return await _proxy(request, user_id, f"/api/jobs/{job_id}/pause", method="POST", raw_body=b"{}")

    @app.post("/api/jobs/{job_id}/resume")
    async def proxy_job_resume(request: Request, job_id: str):
        user_id = require_bearer_auth(request, cfg)
        return await _proxy(request, user_id, f"/api/jobs/{job_id}/resume", method="POST", raw_body=b"{}")

    @app.post("/api/jobs/{job_id}/run")
    async def proxy_job_run(request: Request, job_id: str):
        user_id = require_bearer_auth(request, cfg)
        return await _proxy(request, user_id, f"/api/jobs/{job_id}/run", method="POST", raw_body=b"{}")

    # ------------------------------------------------------------------
    # Admin: user management
    # ------------------------------------------------------------------
    def _require_admin_module_view(request: Request, module_code: str) -> str:
        """Read-side admin gate: is_admin OR role grants ``<module>.index``.

        Lets roles like Accounting open audit-only views of user/department
        lists without being full admins. Write endpoints stay gated by
        :func:`require_admin_auth`.
        """
        user_id = require_bearer_auth(request, cfg)
        try:
            from hermes_cloud.app.permissions import check_permission
            uid = int(user_id)
            if check_permission(uid, module_code, "index"):
                return user_id
        except (TypeError, ValueError, Exception):
            pass
        # Fall through to strict admin check, which raises 403 if not admin.
        require_admin_auth(request, cfg)
        return user_id

    @app.get("/api/admin/users")
    async def admin_list_users(request: Request):
        _require_admin_module_view(request, "admin_users")
        users = app.state.auth_store.list_users() if hasattr(app.state.auth_store, "list_users") else []
        online_only = str(request.query_params.get("online", "")).lower() in ("1", "true", "yes")
        # Enrich with CRM profile fields (realname/mobile/dept/role). Falls
        # back to the minimal shape if CRM schema isn't reachable.
        crm_rows: dict[int, dict] = {}
        profile_totals: dict[int, int] = {}
        try:
            from hermes.crm.db import db_session
            ids = [int(u.id) for u in users]
            if ids:
                with db_session() as db:
                    rows = db.fetchall(
                        """
                        SELECT u.id, u.username, u.realname, u.mobile, u.plan,
                               u.department_id, u.role_id,
                               u.profile_quota AS user_quota,
                               d.name AS department_name,
                               r.name AS role_name,
                               r.profile_quota AS role_quota
                          FROM users u
                          LEFT JOIN departments d ON d.id = u.department_id
                          LEFT JOIN roles r       ON r.id = u.role_id
                         WHERE u.id = ANY(%s)
                        """,
                        (ids,),
                    )
                    # v2 §简化原则: profiles has no user_id column;
                    # JOIN to users via username = profile.id.
                    count_rows = db.fetchall(
                        "SELECT u.id AS uid, COUNT(*) AS n "
                        "FROM profiles p JOIN users u ON u.username = p.id "
                        "WHERE p.is_active = TRUE AND u.id = ANY(%s) "
                        "GROUP BY u.id",
                        (ids,),
                    )
                crm_rows = {int(r["id"]): r for r in (rows or [])}
                profile_totals = {int(r["uid"]): int(r["n"]) for r in (count_rows or [])}
        except Exception:
            logger.debug("admin_list_users: CRM enrichment skipped", exc_info=True)

        # Latest Interaction Log event per user — "人·活动" main-column
        # signal per 新物种宪章 Law 1 (事件是一等公民，状态是派生).
        # We pull one row per user: the most recent interaction where the
        # user appears in actors[].  Shape exposed to UI:
        #   {kind, op, module, target_id, source, at}
        last_action_by_user: dict[int, dict] = {}
        try:
            from hermes.crm.db import db_session
            ids = [int(u.id) for u in users]
            if ids:
                with db_session() as db:
                    act_rows = db.fetchall(
                        """
                        SELECT DISTINCT ON (actor_id)
                               actor_id, kind, content, subjects, source,
                               EXTRACT(EPOCH FROM occurred_at)::float AS ts
                          FROM (
                            SELECT (a->>'id')::bigint AS actor_id,
                                   kind, content, subjects, source, occurred_at
                              FROM interactions,
                                   LATERAL jsonb_array_elements(actors) a
                             WHERE a->>'type' = 'user'
                               AND (a->>'id') ~ '^[0-9]+$'
                               AND (a->>'id')::bigint = ANY(%s)
                          ) x
                         ORDER BY actor_id, occurred_at DESC
                        """,
                        (ids,),
                    )
                for r in (act_rows or []):
                    subj = r.get("subjects") or []
                    first_subj = subj[0] if isinstance(subj, list) and subj else {}
                    content = r.get("content") or {}
                    last_action_by_user[int(r["actor_id"])] = {
                        "kind": r.get("kind"),
                        "op": content.get("op") if isinstance(content, dict) else None,
                        "module": first_subj.get("module") if isinstance(first_subj, dict) else None,
                        "target_id": first_subj.get("id") if isinstance(first_subj, dict) else None,
                        "source": r.get("source"),
                        "at": float(r["ts"]) if r.get("ts") is not None else None,
                    }
        except Exception:
            logger.debug("admin_list_users: last_action enrichment skipped", exc_info=True)

        # Aggregate running-process info by user_id (new-species signal).
        import time as _time
        _now_mono = _time.monotonic()
        _now_wall = _time.time()
        running_by_user: dict[str, dict] = {}
        try:
            for proc in app.state.profile_manager.get_running_processes():
                if not proc.get("running"):
                    continue
                uid = str(proc.get("user_id"))
                bucket = running_by_user.setdefault(uid, {"running": 0, "min_idle": None})
                bucket["running"] += 1
                idle = proc.get("idle_seconds")
                if idle is not None:
                    prev = bucket["min_idle"]
                    bucket["min_idle"] = idle if prev is None else min(prev, idle)
        except Exception:
            logger.debug("admin_list_users: running enrichment skipped", exc_info=True)

        def _agent_status(uid_raw):
            uid_str = str(uid_raw)
            try:
                uid_int = int(uid_raw)
            except (TypeError, ValueError):
                uid_int = None
            r = running_by_user.get(uid_str, {})
            total = profile_totals.get(uid_int, 0) if uid_int is not None else 0
            la = last_action_by_user.get(uid_int) if uid_int is not None else None
            # Prefer the freshest wall-clock signal for "最后活跃":
            # interaction timestamp beats process idle (an agent can have
            # been idle for a while but still logged an action recently
            # via webhook / scheduler).
            last_active = (
                _now_wall - r["min_idle"] if r.get("min_idle") is not None else None
            )
            if la and la.get("at") is not None:
                last_active = la["at"] if last_active is None else max(last_active, la["at"])
            return {
                "running": int(r.get("running", 0)),
                "total": total,
                "last_active_at": last_active,
                "last_action": la,
            }

        def _dept(row):
            if not row or not row.get("department_id"):
                return None
            return {"id": int(row["department_id"]), "name": row.get("department_name") or ""}

        def _role(row):
            if not row or not row.get("role_id"):
                return None
            return {"id": int(row["role_id"]), "name": row.get("role_name") or ""}

        def _quota_info(row):
            """Return {override, role_default, effective} so the admin UI can
            show both layers and a resolved effective cap at a glance."""
            if not row:
                return None
            uq = row.get("user_quota")
            rq = row.get("role_quota")
            eff = uq if uq is not None else (rq if rq is not None else 1)
            return {
                "override": int(uq) if uq is not None else None,
                "role_default": int(rq) if rq is not None else None,
                "effective": int(eff),
            }

        out: list[dict] = []
        for u in users:
            agent = _agent_status(u.id)
            if online_only and agent["running"] == 0:
                continue
            out.append({
                "user_id": u.id,
                # Charter v2 §简化原则: profile.id == users.username.
                # Surfaced so the admin user list can show the profile id.
                "username": getattr(u, "username", None)
                            or (crm_rows.get(int(u.id)) or {}).get("username"),
                "email": u.email,
                "is_active": u.is_active,
                "is_admin": getattr(u, "is_admin", False),
                "created_at": u.created_at.isoformat(),
                "api_key": u.api_key,
                "realname": (crm_rows.get(int(u.id)) or {}).get("realname") or None,
                "mobile": (crm_rows.get(int(u.id)) or {}).get("mobile") or None,
                "plan": (crm_rows.get(int(u.id)) or {}).get("plan") or u.plan,
                "department": _dept(crm_rows.get(int(u.id))),
                "role": _role(crm_rows.get(int(u.id))),
                # Admins are unlimited regardless of stored quota; the UI
                # uses this shape to render "∞ (admin)" vs the numeric cap.
                "profile_quota": (
                    None if getattr(u, "is_admin", False)
                    else _quota_info(crm_rows.get(int(u.id)))
                ),
                "agent_status": agent,
            })
        return JSONResponse({"users": out})

    @app.post("/api/admin/users", status_code=201)
    async def admin_create_user(request: Request):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", "")).strip()
        is_admin_flag = bool(body.get("is_admin", False))
        auto_start = body.get("auto_start", True)
        # Optional CRM-side fields: realname / mobile / department_id / role_id / profile_quota
        realname = body.get("realname")
        mobile = body.get("mobile")
        department_id = body.get("department_id")
        role_id = body.get("role_id")
        try:
            profile_quota_val = _coerce_profile_quota(body.get("profile_quota", ...))
        except HTTPException:
            raise

        if not email or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "email and password are required.",
                                  "type": "invalid_request_error", "code": "missing_fields"}},
            )
        try:
            account = app.state.auth_store.create_user_by_admin(email, password, is_admin=is_admin_flag)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"message": str(exc), "type": "conflict_error", "code": "email_taken"}},
            )

        # Write CRM-side profile fields in one statement.
        crm_updates: dict = {}
        if realname is not None:
            crm_updates["realname"] = (str(realname).strip() or None)
        if mobile is not None:
            crm_updates["mobile"] = (str(mobile).strip() or None)
        if department_id not in (None, ""):
            try: crm_updates["department_id"] = int(department_id)
            except (TypeError, ValueError): pass
        if role_id not in (None, ""):
            try: crm_updates["role_id"] = int(role_id)
            except (TypeError, ValueError): pass
        if profile_quota_val is not ...:
            crm_updates["profile_quota"] = profile_quota_val
        if crm_updates:
            try:
                from hermes.crm.db import db_session
                sets = ", ".join(f"{k} = %s" for k in crm_updates)
                params = list(crm_updates.values()) + [int(account.id)]
                with db_session() as db:
                    db.execute(f"UPDATE users SET {sets} WHERE id = %s", tuple(params))
            except Exception:
                logger.exception("admin_create_user: CRM enrichment failed uid=%s", account.id)

        # Resolve the canonical profile_id FIRST (= users.username slug),
        # then mkdir using that — not the raw bigint user_id. Pre-fix
        # behavior mkdir'd ``profiles/<account.id>/`` (e.g. "2891") AND
        # ``ensure_default_profile`` produced ``profiles/<slug>/`` (e.g.
        # "wangz") on first launch, leaving an empty orphan ``2891/``
        # dir forever. Charter v2 §简化原则: 1 user = 1 profile = 1
        # filesystem dir, slug-named.
        try:
            default_profile = app.state.profile_store.ensure_default_profile(
                user_id=account.id,
            )
        except Exception as exc:
            # Loud failure beats silent orphan-creation. The fallback used
            # to be ``default_profile_id = account.id``, which spawned the
            # 2891/ orphan even when DB write succeeded but read failed
            # on a transient flake. With this raise the admin sees the
            # error, retries, and we never end up with mismatched DB/FS.
            logger.exception(
                "admin_create_user: ensure_default_profile failed uid=%s",
                account.id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {
                    "message": f"Could not create default profile: {exc}",
                    "type": "server_error",
                    "code": "profile_create_failed",
                }},
            )
        default_profile_id = default_profile.id

        # Pre-create the profile directory immediately so scheduled tasks
        # and pre-configured skills can be placed there before the user's
        # first login. Uses the slug-based id (e.g. "wangz"), aligned with
        # what ensure_running will later configure.
        profile_dir = (
            app.state.profile_manager._hermes_home / "profiles" / default_profile_id
        )
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Seed the new profile from the 新用户模板 (SOUL / Personality /
        # skills / model). No-op when no template is configured. Must run
        # before ensure_running so hermes never falls back to the stock
        # Hermes SOUL / empty Personality.
        try:
            _apply_onboarding_template(default_profile_id, str(account.id))
        except Exception:
            logger.exception(
                "admin_create_user: onboarding template apply failed uid=%s",
                account.id,
            )

        if auto_start:
            try:
                await app.state.profile_manager.ensure_running(default_profile_id, user_id=account.id)
            except RuntimeError as exc:
                logger.error("admin_create_user_start_failed uid=%s err=%s", account.id, exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"error": {"message": str(exc), "type": "server_error", "code": "process_unavailable"}},
                )
        return JSONResponse(
            status_code=201,
            content={"user_id": account.id, "api_key": account.api_key, "email": account.email},
        )

    @app.put("/api/admin/users/{user_id}")
    async def admin_update_user(request: Request, user_id: str):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        is_active = body.get("is_active")
        is_admin_val = body.get("is_admin")
        plan = body.get("plan")
        updated = app.state.auth_store.update_user(
            user_id,
            is_active=is_active,
            is_admin=bool(is_admin_val) if is_admin_val is not None else None,
            plan=str(plan) if plan is not None else None,
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "User not found.", "type": "not_found_error", "code": "user_not_found"}},
            )
        # CRM-side fields (realname/mobile/description/department_id/role_id)
        # — applied directly against the users table. None clears the value,
        # absent keys are ignored.
        crm_updates: dict = {}
        if "realname" in body:
            v = body["realname"]
            crm_updates["realname"] = None if v is None else (str(v).strip() or None)
        if "mobile" in body:
            v = body["mobile"]
            crm_updates["mobile"] = None if v is None else (str(v).strip() or None)
        if "description" in body:
            v = body["description"]
            crm_updates["description"] = None if v is None else (str(v).strip() or None)
        if "department_id" in body:
            v = body["department_id"]
            crm_updates["department_id"] = None if v in (None, "") else int(v)
        if "role_id" in body:
            v = body["role_id"]
            crm_updates["role_id"] = None if v in (None, "") else int(v)
        if "profile_quota" in body:
            # Per-user override. null / "" clears the override (delegates
            # back to the role default). The CHECK constraint rejects < 1.
            crm_updates["profile_quota"] = _coerce_profile_quota(body.get("profile_quota"))
        if crm_updates:
            try:
                from hermes.crm.db import db_session
                sets = ", ".join(f"{k} = %s" for k in crm_updates)
                params = list(crm_updates.values()) + [int(user_id)]
                with db_session() as db:
                    db.execute(f"UPDATE users SET {sets} WHERE id = %s", tuple(params))
            except Exception as e:
                logger.exception("admin_update_user: CRM update failed")
                raise HTTPException(status_code=500, detail=f"CRM update failed: {e}")
        return JSONResponse({
            "user_id": updated.id,
            "email": updated.email,
            "is_active": updated.is_active,
            "is_admin": getattr(updated, "is_admin", False),
            "plan": getattr(updated, "plan", None),
        })

    @app.delete("/api/admin/users/{user_id}", status_code=204)
    async def admin_delete_user(request: Request, user_id: str):
        admin_id = require_admin_auth(request, cfg)
        if str(admin_id) == str(user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "管理员不能删除自己的账号。", "type": "invalid_request_error", "code": "cannot_delete_self"}},
            )
        # 1. Stop every running hermes subprocess this user owns.
        try:
            await app.state.profile_manager.stop_user(user_id)
        except Exception:
            logger.exception("admin_delete_user: stop_user failed uid=%s", user_id)
        # 2. Delete profile rows from the profile store. We delete every
        #    profile (default + non-default) by iterating with
        #    include_inactive=True, then dropping rows directly via the
        #    underlying engine to bypass the "default profile is sacred"
        #    guard — the user is being removed wholesale.
        try:
            profile_ids: list[str] = []
            for p in app.state.profile_store.list_profiles(
                user_id=user_id, include_inactive=True,
            ):
                profile_ids.append(p.id)
            from sqlalchemy import text as _sql_text
            engine = getattr(app.state.profile_store, "_engine", None)
            if engine is not None and profile_ids:
                with engine.begin() as conn:
                    conn.execute(
                        _sql_text("DELETE FROM profiles WHERE user_id = :uid"),
                        {"uid": str(user_id)},
                    )
        except Exception:
            logger.exception("admin_delete_user: profile rows cleanup failed uid=%s", user_id)
            profile_ids = []
        # 3. Remove on-disk profile directories so subsequent users with
        #    the same id don't inherit stale state.
        import shutil as _shutil
        hermes_home = app.state.profile_manager._hermes_home
        candidates = set(profile_ids)
        candidates.add(str(user_id))   # default profile dir lives at <home>/profiles/<user_id>/
        for pid in candidates:
            pdir = hermes_home / "profiles" / str(pid)
            if pdir.exists() and pdir.is_dir():
                try:
                    _shutil.rmtree(pdir)
                except Exception:
                    logger.exception(
                        "admin_delete_user: rmtree failed uid=%s pid=%s path=%s",
                        user_id, pid, pdir,
                    )
        # 4. Delete the auth row last so even if the steps above had a
        #    partial failure, the account is gone — the leftover bits
        #    surface in admin logs without ghost-logins.
        deleted = app.state.auth_store.delete_user(user_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "User not found.", "type": "not_found_error", "code": "user_not_found"}},
            )
        return Response(status_code=204)

    @app.post("/api/admin/users/{user_id}/provision")
    async def admin_provision_user(user_id: str, request: Request):
        require_admin_auth(request, cfg)
        import secrets as _secrets
        new_token = _secrets.token_hex(24)
        app.state.auth_store.seed_user_key(token=new_token, user_id=user_id)
        return {"user_id": user_id, "api_key": new_token}

    @app.post("/api/admin/users/{user_id}/impersonate")
    async def admin_impersonate_user(user_id: str, request: Request):
        """Admin: switch the *current browser session* to log in as
        ``user_id``. Issues the same signed cookie used by the password
        login flow, so the SPA boots fresh as the target user on the
        next page load. The admin's own session is overwritten — they
        must sign out and back in to return to the admin account.

        Refuses self-impersonation (no-op) and requires the target user
        to have an ``api_key`` provisioned (the cookie payload binds to
        it for tamper-detection; without one we can't issue a valid
        session). Writes a structured audit log line so the action is
        traceable in /api/admin/audit later.
        """
        admin_id = require_admin_auth(request, cfg)
        if str(admin_id) == str(user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": "Cannot impersonate yourself.",
                    "type": "invalid_request_error",
                    "code": "cannot_impersonate_self",
                }},
            )
        if not cfg.session_cookie_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {
                    "message": "Session cookie secret not configured on this server.",
                    "type": "service_unavailable",
                    "code": "session_cookies_disabled",
                }},
            )
        target = app.state.auth_store.get_user(user_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "User not found.", "type": "not_found_error", "code": "user_not_found"}},
            )
        api_key = getattr(target, "api_key", None)
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {
                    "message": "Target user has no api_key — provision one first.",
                    "type": "invalid_state_error",
                    "code": "no_api_key",
                }},
            )
        logger.info(
            "admin_impersonate admin_id=%s target_user_id=%s target_email=%s",
            admin_id, target.id, getattr(target, "email", None),
        )
        response = JSONResponse({
            "user_id": target.id,
            "email": getattr(target, "email", None),
        })
        set_session_cookie(response, cfg.session_cookie_secret, target.id, api_key)
        return response

    @app.post("/api/admin/users/{user_id}/reset-password")
    async def admin_reset_password(user_id: str, request: Request):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        new_password = str(body.get("password", "")).strip()
        if len(new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "Password must be at least 8 characters.",
                                  "type": "invalid_request_error", "code": "password_too_short"}},
            )
        ok = app.state.auth_store.reset_password(user_id, new_password)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "User not found.", "type": "not_found_error", "code": "user_not_found"}},
            )
        return {"user_id": user_id, "ok": True}

    @app.get("/api/admin/processes")
    async def admin_list_processes(request: Request):
        require_admin_auth(request, cfg)
        # Enrich each process entry with the user's email from auth_store.
        uid_to_email = {
            u.id: u.email
            for u in (app.state.auth_store.list_users()
                      if hasattr(app.state.auth_store, "list_users") else [])
        }
        processes = [
            {**p, "email": uid_to_email.get(p["user_id"], "")}
            for p in app.state.profile_manager.get_running_processes()
        ]
        return JSONResponse({
            "processes": processes,
            "free_ports": len(app.state.profile_manager._free_ports),
        })

    @app.delete("/api/admin/processes/{user_id}")
    async def admin_stop_process(user_id: str, request: Request):
        """Stop all of a user's running profiles **except the default one**.

        The default profile is load-bearing — every user must always have
        one running process for chat/mail/etc. to land somewhere, and
        the default is the profile that auto-starts on user creation.
        Admins that need to truly kill a user's process should delete
        the user, which cascades.
        """
        require_admin_auth(request, cfg)
        store = app.state.profile_store
        manager = app.state.profile_manager
        stopped: list[str] = []
        skipped: list[str] = []
        for p in store.list_profiles(user_id=user_id):
            if not p.is_active:
                continue
            if p.is_default:
                skipped.append(p.id)
                continue
            await manager.stop_profile(p.id)
            stopped.append(p.id)
        return JSONResponse({"ok": True, "stopped": stopped, "skipped_default": skipped})

    @app.post("/api/admin/processes/profile/{profile_id}/start")
    async def admin_start_profile_process(profile_id: str, request: Request):
        """Start a single profile's hermes subprocess.

        Counterpart to ``admin_stop_profile_process``: spawns the
        subprocess if it isn't already running. Looks up the owning
        user_id from the profile row so ownership tracking is correct
        (needed for ``stop_user`` and audit logs).
        """
        require_admin_auth(request, cfg)
        profile = app.state.profile_store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"message": "Profile not found.",
                                  "type": "not_found_error",
                                  "code": "profile_not_found"}},
            )
        try:
            port = await app.state.profile_manager.ensure_running(
                profile_id, user_id=profile.user_id,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"message": str(exc),
                                  "type": "server_error",
                                  "code": "process_unavailable"}},
            )
        return JSONResponse({"ok": True, "port": port})

    @app.delete("/api/admin/processes/profile/{profile_id}")
    async def admin_stop_profile_process(profile_id: str, request: Request):
        """Stop a single profile's hermes subprocess.

        Charter v2 §简化原则: 1 user ↔ 1 profile, so every profile is
        the user's default — the previous "cannot stop default profile"
        guard meant every stop request 409'd, effectively disabling
        the admin "停止" button. We now always honor the stop; the
        process auto-respawns on the next request.
        """
        require_admin_auth(request, cfg)
        await app.state.profile_manager.stop_profile(profile_id)
        return JSONResponse({"ok": True})

    @app.get("/api/admin/users/{user_id}/detail")
    def admin_user_detail(user_id: str, request: Request):
        """Get user detail: basic info + profiles + process status."""
        require_admin_auth(request, cfg)
        # User info
        user_info = app.state.auth_store.get_user(user_id) if app.state.auth_store else None
        if not user_info:
            raise HTTPException(status_code=404, detail={"error": {"message": "User not found."}})
        # Profiles (agents)
        profiles = app.state.profile_store.list_profiles(user_id=user_id)
        # Running processes — index all by profile_id, then match per-profile
        proc_by_profile = {
            str(p["profile_id"]): p
            for p in app.state.profile_manager.get_running_processes()
        }
        # Attach process info to each profile
        enriched_profiles = []
        for p in profiles:
            d = _profile_to_dict(p)
            proc = proc_by_profile.get(str(p.id))
            d["process"] = {
                "running": proc is not None,
                "port": proc.get("port") if proc else None,
                "pid": proc.get("pid") if proc else None,
                "idle_seconds": proc.get("idle_seconds") if proc else None,
                "multiplexed": bool(proc.get("multiplexed")) if proc else False,
            }
            enriched_profiles.append(d)
        return JSONResponse({
            "user": {
                "id": user_info.id,
                "email": user_info.email,
                "is_admin": user_info.is_admin,
                "is_active": user_info.is_active,
                "created_at": str(user_info.created_at) if user_info.created_at else None,
            },
            "profiles": enriched_profiles,
        })

    @app.get("/api/admin/users/{user_id}/recent_actions")
    def admin_user_recent_actions(user_id: str, request: Request):
        """Interaction Log tail + today's projections for one user.

        Used by the admin list hover-card and the detail drawer so we
        render the event stream directly (宪章 Law 1) and let derived
        metrics sit in a clearly secondary slot.
        """
        require_admin_auth(request, cfg)
        try:
            uid_int = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail={"error": {"message": "invalid user_id"}})
        limit = max(1, min(50, int(request.query_params.get("limit", 10))))

        events: list[dict] = []
        today_counts: dict[str, int] = {}
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                rows = db.fetchall(
                    """
                    SELECT kind, content, subjects, source,
                           EXTRACT(EPOCH FROM occurred_at)::float AS ts
                      FROM interactions,
                           LATERAL jsonb_array_elements(actors) a
                     WHERE a->>'type' = 'user'
                       AND (a->>'id') ~ '^[0-9]+$'
                       AND (a->>'id')::bigint = %s
                     ORDER BY occurred_at DESC
                     LIMIT %s
                    """,
                    (uid_int, limit),
                )
                for r in (rows or []):
                    subj = r.get("subjects") or []
                    first_subj = subj[0] if isinstance(subj, list) and subj else {}
                    content = r.get("content") or {}
                    events.append({
                        "kind": r.get("kind"),
                        "op": content.get("op") if isinstance(content, dict) else None,
                        "fields": content.get("fields") if isinstance(content, dict) else None,
                        "module": first_subj.get("module") if isinstance(first_subj, dict) else None,
                        "target_id": first_subj.get("id") if isinstance(first_subj, dict) else None,
                        "source": r.get("source"),
                        "at": float(r["ts"]) if r.get("ts") is not None else None,
                    })
                # Today's projection — counts by (kind, op) since local midnight UTC.
                # Projections are派生, kept in a single row so UI can render a thin
                # summary line without doing math in JS.
                count_rows = db.fetchall(
                    """
                    SELECT kind,
                           COALESCE(content->>'op', '') AS op,
                           COUNT(*) AS n
                      FROM interactions,
                           LATERAL jsonb_array_elements(actors) a
                     WHERE a->>'type' = 'user'
                       AND (a->>'id') ~ '^[0-9]+$'
                       AND (a->>'id')::bigint = %s
                       AND occurred_at >= date_trunc('day', now())
                     GROUP BY kind, op
                    """,
                    (uid_int,),
                )
                for r in (count_rows or []):
                    key = f"{r.get('kind') or ''}:{r.get('op') or ''}".rstrip(":")
                    today_counts[key] = int(r.get("n") or 0)
        except Exception:
            logger.debug("admin_user_recent_actions query failed", exc_info=True)

        return JSONResponse({
            "events": events,
            "today": {
                "total": sum(today_counts.values()),
                "by_kind": today_counts,
            },
        })

    # ------------------------------------------------------------------
    # Admin: departments / roles / permissions (kacrm org tables)
    # ------------------------------------------------------------------
    @app.get("/api/admin/departments")
    async def admin_list_departments(request: Request):
        _require_admin_module_view(request, "admin_departments")
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                rows = db.fetchall(
                    """
                    SELECT d.id, d.name, d.parent_id, d.manager_ids,
                           d.sort_order, d.description,
                           COUNT(u.id) FILTER (WHERE u.deleted = FALSE) AS member_count
                      FROM departments d
                      LEFT JOIN users u ON u.department_id = d.id
                     GROUP BY d.id
                     ORDER BY d.sort_order, d.name
                    """
                )
        except Exception as e:
            logger.exception("admin_list_departments failed")
            raise HTTPException(status_code=500, detail=f"list failed: {e}")
        return JSONResponse({"departments": [
            {
                "id": int(r["id"]),
                "name": r.get("name") or "",
                "parent_id": int(r["parent_id"]) if r.get("parent_id") else None,
                "manager_ids": r.get("manager_ids") or [],
                "sort_order": int(r.get("sort_order") or 0),
                "description": r.get("description") or None,
                "member_count": int(r.get("member_count") or 0),
            } for r in (rows or [])
        ]})

    @app.post("/api/admin/departments", status_code=201)
    async def admin_create_department(request: Request):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        parent_id = body.get("parent_id")
        pid = int(parent_id) if parent_id not in (None, "") else None
        desc = body.get("description")
        mgr_ids = _normalize_manager_ids(body.get("manager_ids"))
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                mgr_ids = _filter_existing_user_ids(db, mgr_ids)
                row = db.fetchone(
                    "INSERT INTO departments (name, parent_id, description, sort_order, manager_ids) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id",
                    (name, pid, (str(desc).strip() if desc else None),
                     int(body.get("sort_order") or 0), json.dumps(mgr_ids)),
                )
            return JSONResponse(status_code=201, content={"id": int(row["id"]), "name": name})
        except Exception as e:
            logger.exception("admin_create_department failed")
            raise HTTPException(status_code=500, detail=f"create failed: {e}")

    @app.put("/api/admin/departments/{dept_id}")
    async def admin_update_department(request: Request, dept_id: int):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        updates: dict = {}
        if "name" in body:
            n = str(body["name"]).strip()
            if not n:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            updates["name"] = n
        if "parent_id" in body:
            v = body["parent_id"]
            updates["parent_id"] = int(v) if v not in (None, "") else None
        if "description" in body:
            v = body["description"]
            updates["description"] = str(v).strip() if v else None
        if "sort_order" in body:
            updates["sort_order"] = int(body["sort_order"] or 0)
        # manager_ids:jsonb 数组,单独标记以便加 ::jsonb cast(动态 SET 默认无 cast)。
        set_managers = "manager_ids" in body
        if not updates and not set_managers:
            raise HTTPException(status_code=400, detail="no editable fields")
        if updates.get("parent_id") == dept_id:
            raise HTTPException(status_code=400, detail="department cannot be its own parent")
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                set_clauses = [f"{k} = %s" for k in updates]
                params = list(updates.values())
                if set_managers:
                    mgr_ids = _filter_existing_user_ids(
                        db, _normalize_manager_ids(body["manager_ids"]))
                    set_clauses.append("manager_ids = %s::jsonb")
                    params.append(json.dumps(mgr_ids))
                params.append(dept_id)
                db.execute(
                    f"UPDATE departments SET {', '.join(set_clauses)} WHERE id = %s",
                    tuple(params))
            return JSONResponse({"ok": True})
        except Exception as e:
            logger.exception("admin_update_department failed")
            raise HTTPException(status_code=500, detail=f"update failed: {e}")

    @app.delete("/api/admin/departments/{dept_id}", status_code=204)
    async def admin_delete_department(request: Request, dept_id: int):
        require_admin_auth(request, cfg)
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                in_use = db.fetchcolumn(
                    "SELECT COUNT(*) FROM users WHERE department_id = %s AND deleted = FALSE",
                    (dept_id,),
                )
                if (in_use or 0) > 0:
                    raise HTTPException(status_code=409, detail="department has members; reassign first")
                has_children = db.fetchcolumn(
                    "SELECT COUNT(*) FROM departments WHERE parent_id = %s", (dept_id,)
                )
                if (has_children or 0) > 0:
                    raise HTTPException(status_code=409, detail="department has sub-departments")
                db.execute("DELETE FROM departments WHERE id = %s", (dept_id,))
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("admin_delete_department failed")
            raise HTTPException(status_code=500, detail=f"delete failed: {e}")
        return Response(status_code=204)

    # ---- Pipeline milestones admin endpoints — REMOVED 2026-05-25 ----
    # `pipeline_milestones` 字典表已下线，milestone 选项统一回到 module_fields
    # 的 picklist；won / lost 在 field_config.system_options 里标记为不可
    # 删除/改名 value。SPA 后台「管道阶段管理」入口同步删除，admin 在
    # 「Schema → 商机 → milestone」字段属性面板里管理选项即可。
    # 历史：docs/2026-05-12-opportunity-pipeline-governance-plan.md（已废止）


    # ---- Lost reasons -----------------------------------------------

    _LOST_REASON_EDITABLE = {"name", "label", "sort_order", "is_active"}

    @app.get("/api/admin/lost-reasons")
    async def admin_list_lost_reasons(request: Request):
        _require_admin_module_view(request, "admin_pipeline")
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                rows = db.fetchall(
                    """
                    SELECT id, name, label, sort_order, is_active,
                           EXTRACT(EPOCH FROM created_at)::float AS created_ts,
                           EXTRACT(EPOCH FROM updated_at)::float AS updated_ts
                      FROM lost_reasons
                     ORDER BY sort_order, name
                    """
                )
        except Exception as e:
            logger.exception("admin_list_lost_reasons failed")
            raise HTTPException(status_code=500, detail=f"list failed: {e}")
        return JSONResponse({"reasons": [
            {
                "id": int(r["id"]),
                "name": r["name"],
                "label": r["label"],
                "sort_order": int(r["sort_order"]),
                "is_active": bool(r["is_active"]),
                "created_at": float(r["created_ts"]) if r.get("created_ts") is not None else None,
                "updated_at": float(r["updated_ts"]) if r.get("updated_ts") is not None else None,
            } for r in (rows or [])
        ]})

    @app.post("/api/admin/lost-reasons", status_code=201)
    async def admin_create_lost_reason(request: Request):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        name = str(body.get("name", "")).strip()
        label = str(body.get("label", "")).strip()
        if not name or not label:
            raise HTTPException(status_code=400, detail="name and label are required")
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                row = db.fetchone(
                    """
                    INSERT INTO lost_reasons (name, label, sort_order, is_active)
                    VALUES (%s, %s, %s, %s) RETURNING id
                    """,
                    (name, label, int(body.get("sort_order") or 0),
                     bool(body.get("is_active", True))),
                )
            return JSONResponse(status_code=201, content={"id": int(row["id"]), "name": name})
        except Exception as e:
            msg = str(e)
            if "lost_reasons_name_key" in msg or "duplicate key" in msg:
                raise HTTPException(status_code=409, detail=f"lost reason name already exists: {name}")
            logger.exception("admin_create_lost_reason failed")
            raise HTTPException(status_code=500, detail=f"create failed: {e}")

    @app.put("/api/admin/lost-reasons/{reason_id}")
    async def admin_update_lost_reason(request: Request, reason_id: int):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        updates: dict = {}
        for k in body:
            if k not in _LOST_REASON_EDITABLE:
                if k in {"id", "created_at", "updated_at"}:
                    continue
                raise HTTPException(status_code=400, detail=f"unknown field: {k}")
            v = body[k]
            if k in ("name", "label"):
                v = str(v).strip()
                if not v:
                    raise HTTPException(status_code=400, detail=f"{k} cannot be empty")
            elif k == "sort_order":
                v = int(v or 0)
            elif k == "is_active":
                v = bool(v)
            updates[k] = v
        if not updates:
            raise HTTPException(status_code=400, detail="no editable fields")
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                exists = db.fetchcolumn(
                    "SELECT 1 FROM lost_reasons WHERE id = %s", (reason_id,)
                )
                if not exists:
                    raise HTTPException(status_code=404, detail="lost reason not found")
                sets = ", ".join(f"{k} = %s" for k in updates)
                params = list(updates.values()) + [reason_id]
                db.execute(f"UPDATE lost_reasons SET {sets} WHERE id = %s", tuple(params))
            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except Exception as e:
            msg = str(e)
            if "lost_reasons_name_key" in msg or "duplicate key" in msg:
                raise HTTPException(status_code=409, detail="lost reason name already exists")
            logger.exception("admin_update_lost_reason failed")
            raise HTTPException(status_code=500, detail=f"update failed: {e}")

    @app.delete("/api/admin/lost-reasons/{reason_id}", status_code=204)
    async def admin_delete_lost_reason(request: Request, reason_id: int):
        require_admin_auth(request, cfg)
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                cur = db.fetchone(
                    "SELECT name FROM lost_reasons WHERE id = %s", (reason_id,)
                )
                if not cur:
                    raise HTTPException(status_code=404, detail="lost reason not found")
                # PR-2 will add an FK; until then check by name.
                in_use = db.fetchcolumn(
                    "SELECT COUNT(*) FROM potentials WHERE lost_reason = %s AND deleted = FALSE",
                    (cur["name"],),
                )
                if (in_use or 0) > 0:
                    raise HTTPException(
                        status_code=409,
                        detail=f"lost reason '{cur['name']}' is in use by {in_use} opportunities; reassign or deactivate first",
                    )
                db.execute("DELETE FROM lost_reasons WHERE id = %s", (reason_id,))
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("admin_delete_lost_reason failed")
            raise HTTPException(status_code=500, detail=f"delete failed: {e}")
        return Response(status_code=204)

    # ---- Roles + permissions -----------------------------------------

    @app.get("/api/admin/roles")
    async def admin_list_roles(request: Request):
        _require_admin_module_view(request, "admin_roles")
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                rows = db.fetchall(
                    """
                    SELECT r.id, r.name, r.is_default, r.description, r.profile_quota,
                           COUNT(u.id) FILTER (WHERE u.deleted = FALSE) AS member_count
                      FROM roles r
                      LEFT JOIN users u ON u.role_id = r.id
                     GROUP BY r.id
                     ORDER BY r.is_default DESC, r.id
                    """
                )
        except Exception as e:
            logger.exception("admin_list_roles failed")
            raise HTTPException(status_code=500, detail=f"list failed: {e}")
        return JSONResponse({"roles": [
            {
                "id": int(r["id"]),
                "name": r.get("name") or "",
                "is_default": bool(r.get("is_default")),
                "description": r.get("description") or None,
                "member_count": int(r.get("member_count") or 0),
                # NULL → frontend renders "无限"; frontend posts null to clear it.
                "profile_quota": (
                    int(r["profile_quota"]) if r.get("profile_quota") is not None else None
                ),
            } for r in (rows or [])
        ]})

    def _coerce_profile_quota(raw):
        """Parse an optional profile-quota field from a JSON body.

        Accepts missing → no-change sentinel ``...``; ``None``/``""`` → NULL
        (unlimited); any integer ≥ 1 → clamp to int. Raises 400 on a
        negative or zero value to match the DB CHECK constraint.
        """
        if raw is ...:
            return ...
        if raw is None or raw == "":
            return None
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="profile_quota must be an integer or null",
            )
        if n < 1:
            raise HTTPException(
                status_code=400,
                detail="profile_quota must be >= 1 (or null for unlimited)",
            )
        return n

    @app.post("/api/admin/roles", status_code=201)
    async def admin_create_role(request: Request):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        quota = _coerce_profile_quota(body.get("profile_quota", ...))
        try:
            from hermes.crm.models.roles_db import RolesDb
            role_id = RolesDb().insert({
                "name": name,
                "description": (str(body.get("description", "")).strip() or None),
                "is_default": False,
            })
            # RolesDb may not know about profile_quota — set it in a second
            # statement so the initial insert keeps working regardless of
            # whether the ORM model was regenerated.
            if quota is not ...:
                from hermes.crm.db import db_session
                with db_session() as db:
                    db.execute(
                        "UPDATE roles SET profile_quota = %s WHERE id = %s",
                        (quota, int(role_id)),
                    )
            return JSONResponse(status_code=201, content={"id": int(role_id), "name": name})
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("admin_create_role failed")
            raise HTTPException(status_code=500, detail=f"create failed: {e}")

    @app.put("/api/admin/roles/{role_id}")
    async def admin_update_role(request: Request, role_id: int):
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        name = body.get("name")
        desc = body.get("description")
        quota = _coerce_profile_quota(body.get("profile_quota", ...))
        if name is None and desc is None and quota is ...:
            raise HTTPException(status_code=400, detail="no editable fields")
        try:
            from hermes.crm.models.roles_db import RolesDb
            if name is not None or desc is not None:
                RolesDb().update(role_id, {
                    "name": (str(name).strip() if name is not None else None),
                    "description": (str(desc).strip() if desc else None),
                })
            if quota is not ...:
                from hermes.crm.db import db_session
                with db_session() as db:
                    db.execute(
                        "UPDATE roles SET profile_quota = %s WHERE id = %s",
                        (quota, int(role_id)),
                    )
            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("admin_update_role failed")
            raise HTTPException(status_code=500, detail=f"update failed: {e}")

    @app.delete("/api/admin/roles/{role_id}", status_code=204)
    async def admin_delete_role(request: Request, role_id: int):
        require_admin_auth(request, cfg)
        try:
            from hermes.crm.models.roles_db import RolesDb
            rdb = RolesDb()
            if rdb.is_role_in_use(role_id):
                raise HTTPException(status_code=409, detail="role is assigned to users; reassign first")
            rdb.delete(role_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("admin_delete_role failed")
            raise HTTPException(status_code=500, detail=f"delete failed: {e}")
        return Response(status_code=204)

    @app.get("/api/admin/roles/{role_id}/permissions")
    async def admin_get_role_permissions(request: Request, role_id: int):
        """Return the full permission matrix for a role.

        Shape: ``{role, modules: [{id, name}], permissions: [{id, code, name}],
        grants: {module_id: {permission_id: true}}}``. The SPA builds a grid
        from this: rows=modules, columns=permissions, checkbox=grant.
        """
        require_admin_auth(request, cfg)
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                role = db.fetchone(
                    "SELECT id, name, description FROM roles WHERE id = %s", (role_id,)
                )
                if not role:
                    raise HTTPException(status_code=404, detail="role not found")
                modules = db.fetchall(
                    "SELECT id, name, title, kind, description "
                    "FROM collections ORDER BY kind, id"
                )
                perms = db.fetchall("SELECT id, code, name FROM permissions ORDER BY id")
                grants = db.fetchall(
                    "SELECT module_id, permission_id, has_permission "
                    "FROM role_permissions WHERE role_id = %s",
                    (role_id,),
                )
            grant_map: dict = {}
            for g in (grants or []):
                if g.get("has_permission"):
                    grant_map.setdefault(str(int(g["module_id"])), {})[str(int(g["permission_id"]))] = True
            return JSONResponse({
                "role": {
                    "id": int(role["id"]),
                    "name": role.get("name") or "",
                    "description": role.get("description") or None,
                },
                "modules": [
                    {
                        "id": int(m["id"]),
                        "name": m.get("name") or "",
                        "title": m.get("title") or "",
                        "kind": m.get("kind") or "crm",
                        "description": m.get("description") or None,
                    }
                    for m in (modules or [])
                ],
                "permissions": [
                    {"id": int(p["id"]), "code": p.get("code") or "", "name": p.get("name") or ""}
                    for p in (perms or [])
                ],
                "grants": grant_map,
            })
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("admin_get_role_permissions failed")
            raise HTTPException(status_code=500, detail=f"fetch failed: {e}")

    @app.put("/api/admin/roles/{role_id}/permissions")
    async def admin_set_role_permissions(request: Request, role_id: int):
        """Replace the permission grants for a role.

        Body: ``{grants: {module_id: [permission_id, ...]}}`` — only granted
        entries are listed; everything else is cleared.
        """
        require_admin_auth(request, cfg)
        body = await _parse_json_body(request)
        grants = body.get("grants") or {}
        if not isinstance(grants, dict):
            raise HTTPException(status_code=400, detail="grants must be an object")
        pairs: list[tuple[int, int]] = []
        for mid, perm_list in grants.items():
            if not isinstance(perm_list, list):
                raise HTTPException(status_code=400, detail="each grants value must be a list")
            try:
                m = int(mid)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"bad module id {mid!r}")
            for pid in perm_list:
                try:
                    pairs.append((m, int(pid)))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail=f"bad permission id {pid!r}")
        try:
            from hermes.crm.db import db_session
            with db_session() as db:
                db.execute(
                    "DELETE FROM role_permissions WHERE role_id = %s", (role_id,)
                )
                if pairs:
                    vals = ", ".join(["(%s, %s, %s, TRUE)"] * len(pairs))
                    flat: list = []
                    for m, p in pairs:
                        flat.extend([role_id, p, m])
                    db.execute(
                        "INSERT INTO role_permissions (role_id, permission_id, module_id, has_permission) "
                        "VALUES " + vals,
                        tuple(flat),
                    )
            return JSONResponse({"ok": True, "count": len(pairs)})
        except Exception as e:
            logger.exception("admin_set_role_permissions failed")
            raise HTTPException(status_code=500, detail=f"save failed: {e}")

    # ------------------------------------------------------------------
    # Agent message ingestion (Charter §一定律 1 + 原语 ①).
    #
    # Hermes per-profile subprocesses POST every agent conversation message
    # here so it lands in the canonical Interaction Log instead of a
    # per-machine state.db sqlite file. See
    # docs/2026-04-25-agent-message-as-event.md for the schema this
    # endpoint enforces.
    #
    # Idempotency: (profile_id, session_id, message_id) is the dedup key.
    # A retry from hermes (network blip, restart) returning the same
    # payload returns the existing interaction_id with deduplicated=True
    # rather than creating a duplicate row.
    # ------------------------------------------------------------------
    @app.post("/internal/agent/event")
    async def internal_agent_event(request: Request):
        _require_internal_key(request)
        body = await _parse_json_body(request)

        profile_id = str(body.get("profile_id") or "").strip()
        session_id = str(body.get("session_id") or "").strip()
        message_id = str(body.get("message_id") or "").strip()
        agent_name = str(body.get("agent_name") or "").strip()
        role = str(body.get("role") or "").strip()
        text = body.get("text")
        if not (profile_id and session_id and message_id and agent_name
                and role and isinstance(text, str)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": ("profile_id, session_id, message_id, "
                                "agent_name, role, text are required."),
                    "type": "invalid_request_error",
                    "code": "missing_fields",
                }},
            )

        # Idempotent shortcut: if this (profile_id, session_id, message_id)
        # tuple is already in interactions, return its id without re-emitting.
        # We key on content fields rather than a separate uniqueness constraint
        # so the Interaction Log stays append-only with no per-kind schema.
        from hermes.crm.db import db_session as _db_session
        from hermes_cloud.app.interactions.emitter import emit as _emit

        with _db_session() as db:
            existing = db.fetchone(
                """
                SELECT id::text AS id FROM interactions
                 WHERE kind = 'agent.message'
                   AND content->>'session_id' = %s
                   AND content->>'message_id' = %s
                   AND source = %s
                 LIMIT 1
                """,
                (session_id, message_id, f"agent:{profile_id}:{agent_name}"),
            )
        if existing:
            return JSONResponse({"data": {
                "interaction_id": existing["id"],
                "deduplicated": True,
            }})

        # Build the actors list. The agent actor is always present (validator
        # enforces it); user actor is optional — system / tool messages aren't
        # tied to a specific user turn.
        actors: list[dict] = [{
            "type": "agent", "name": agent_name, "profile_id": profile_id,
        }]
        user_id = body.get("user_id")
        if user_id is not None:
            try:
                actors.append({"type": "user", "id": int(user_id)})
            except (TypeError, ValueError):
                pass  # malformed user_id; ignore rather than 400 — agent might
                       # have sent a string for an external user

        # Compose the content payload. The validator
        # (emitter._validate_agent_message) will reject anything malformed.
        content: dict = {
            "session_id": session_id,
            "message_id": message_id,
            "role": role,
            "text": text,
        }
        for opt_key in (
            "tool_calls", "tool_call_id", "tool_name",
            "reasoning", "reasoning_details", "finish_reason",
            "tokens", "model", "billing", "estimated_cost_usd",
        ):
            v = body.get(opt_key)
            if v is not None:
                content[opt_key] = v

        # occurred_at: hermes-supplied epoch float or ISO string; fall back
        # to now() if absent.
        occurred_raw = body.get("occurred_at")
        occurred_at: Optional[dt.datetime] = None
        if isinstance(occurred_raw, (int, float)):
            try:
                occurred_at = dt.datetime.fromtimestamp(
                    float(occurred_raw), tz=dt.timezone.utc,
                )
            except (OverflowError, ValueError):
                occurred_at = None
        elif isinstance(occurred_raw, str) and occurred_raw:
            try:
                occurred_at = dt.datetime.fromisoformat(
                    occurred_raw.replace("Z", "+00:00"),
                )
            except ValueError:
                occurred_at = None

        subjects = body.get("subjects") if isinstance(body.get("subjects"), list) else []
        derived_from = body.get("derived_from") or None

        try:
            interaction_id = _emit(
                kind="agent.message",
                actors=actors,
                subjects=subjects,
                content=content,
                source=f"agent:{profile_id}:{agent_name}",
                occurred_at=occurred_at,
                derived_from=derived_from,
            )
        except ValueError as exc:
            # Validator rejected the payload — surface as 400 with the
            # specific reason so the hermes side can fix and retry.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "agent_message_invalid",
                }},
            )

        return JSONResponse({"data": {
            "interaction_id": interaction_id,
            "deduplicated": False,
        }})

    # ------------------------------------------------------------------
    # Charter v2 §定律 4 — celery worker has no FastAPI lifecycle, so
    # cron-driven utility tasks (orphan_commitment_scan, etc) post
    # their LLM-extraction payload here. We drive the user's main
    # agent + the named skill, then return the parsed JSON to the
    # worker. Worker keeps the DB query / hint-upsert logic local.
    # ------------------------------------------------------------------
    @app.post("/internal/utility/run-skill")
    async def internal_utility_run_skill(request: Request):
        _require_internal_key(request)
        body = await _parse_json_body(request)

        user_id_raw = body.get("user_id")
        skill_name = str(body.get("skill_name") or "").strip()
        instruction = str(body.get("instruction") or "")
        timeout_sec = float(body.get("timeout_sec") or 60.0)
        if not skill_name or not instruction or user_id_raw is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": "user_id, skill_name, instruction required",
                    "type": "invalid_request_error",
                    "code": "missing_fields",
                }},
            )
        try:
            user_id_int = int(user_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"message": "user_id must be int",
                                  "type": "invalid_request_error",
                                  "code": "bad_user_id"}},
            )

        from hermes_cloud.app.utility_via_skill import (
            UtilitySkillError, run_utility_skill,
        )
        try:
            reply = await run_utility_skill(
                app=app, user_id=user_id_int,
                skill_name=skill_name,
                instruction=instruction,
                timeout_sec=timeout_sec,
                raise_on_error=True,
            )
        except UtilitySkillError as exc:
            # Charter v2 §一系统: never silently dress up a downstream
            # LLM/transport failure as 200 OK + null reply. The cron
            # scheduler reads this status — empty reply means "agent
            # decided 0 results", a real error means "agent never ran".
            raise HTTPException(
                status_code=exc.status_code,
                detail={"error": {
                    "message": f"utility_via_skill: {exc.reason}",
                    "type": "utility_skill_error",
                    "code": "skill_dispatch_failed",
                    "downstream_body": exc.body,
                }},
            ) from exc
        return JSONResponse({"reply": reply})

    # /internal/cron/run-coach-job removed 2026-05-19. Hermes cron now
    # runs daily-coach / weekly-digest in NATIVE skill mode (no script,
    # no HTTP self-call). The skill emits each card via the
    # ``coach_proposal_submit`` tool (hermes-patches/49) which routes
    # through Action Gateway; the skill's final response (human
    # summary) is delivered by hermes cron itself to ``deliver=
    # "weixin,feishu"``. See hermes_cloud.app.cron_provisioner.

    # ------------------------------------------------------------------
    # Unified channel push — cloud-side jobs (coach hints, weekly
    # reports) call this to deliver a message through whichever
    # platform the user is bound to. Works for any platform whose
    # per-profile hermes adapter exposes ``adapter.send(chat_id, text)``
    # (feishu / weixin / slack / telegram / dingtalk / …). The cloud
    # looks up (or starts) the user's hermes subprocess, then forwards
    # the request to that subprocess's ``POST /internal/channel/push``.
    # ------------------------------------------------------------------
    @app.post("/internal/channel/push")
    async def internal_channel_push(request: Request):
        _require_internal_key(request)
        body = await _parse_json_body(request)

        user_id = str(body.get("user_id") or "").strip()
        profile_id = str(body.get("profile_id") or "").strip() or user_id
        platform = (body.get("platform") or "").strip().lower()
        channel_id = (body.get("channel_id") or "").strip()
        text = body.get("text")
        if not (user_id and platform and channel_id
                and isinstance(text, str) and text):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {
                    "message": "user_id, platform, channel_id, text are required.",
                    "type": "invalid_request_error",
                    "code": "missing_fields",
                }},
            )

        # Every channel — including feishu/lark — routes to the user's own
        # per-profile hermes subprocess, which sends via its own native
        # adapter using that profile's config.yaml credentials.
        pm = getattr(app.state, "profile_manager", None)
        if pm is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"message": "profile_manager unavailable.",
                                  "type": "service_unavailable",
                                  "code": "profile_manager_unavailable"}},
            )
        try:
            port = await pm.ensure_running(profile_id, user_id=user_id)
        except Exception as exc:
            logger.exception(
                "channel push: ensure_running failed profile=%s", profile_id,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": {"message": f"profile start failed: {exc}",
                                  "type": "upstream_error",
                                  "code": "profile_start_failed"}},
            )

        url = f"http://127.0.0.1:{port}{pm.mux_path_prefix(profile_id)}/internal/channel/push"
        try:
            # iLink WS push to QQ servers can take 5-15s round-trip; 10s
            # was timing out coach pushes (especially when the message
            # body is long and contains multiple cards).
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    json={"platform": platform, "channel_id": channel_id,
                          "text": text},
                    headers={"Authorization": f"Bearer {cfg.internal_key}"},
                )
        except Exception as exc:
            logger.exception("channel push: hermes call failed port=%s", port)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": {"message": f"hermes push failed: {exc}",
                                  "type": "upstream_error",
                                  "code": "hermes_push_failed"}},
            )
        try:
            payload = resp.json()
        except Exception:
            payload = {"ok": False, "error": resp.text[:500]}
        return JSONResponse(payload, status_code=resp.status_code)

    # ------------------------------------------------------------------
    # CRM subsystem (phase 0) — mounted under /crm
    # ------------------------------------------------------------------
    try:
        from hermes_cloud.app.crm.routes import build_router as _build_crm_router
        app.include_router(_build_crm_router())
    except Exception as exc:  # pragma: no cover — keep hermes bootable if CRM DB is down
        logger.warning("crm: router not mounted (%s)", exc)

    # ------------------------------------------------------------------
    # CRM server-rendered pages + static assets (ported from kacrm)
    # DISABLED: conversation-first CRM — all CRM interaction happens
    # through the agent's tool calls, not through the legacy kacrm UI.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 2 — Gateway / Trust APIs (only when CRM PG reachable)
    # Charter v2 Phase 3 (2026-05-06): /api/agents/* /api/rl/*
    # /api/feedback/* / pool_api dashboard endpoints all removed
    # along with the agents + rl + feedback subsystems.
    # ------------------------------------------------------------------
    if _crm_up:
        try:
            from hermes_cloud.app.gateway.api import router as _gw_router
            from hermes_cloud.app.trust.api import router as _trust_router

            from hermes_cloud.app.interactions.api import router as _interactions_router
            from hermes_cloud.app.touchpoints.api import router as _touchpoints_router
            # Ensure all touchpoint adapters are registered
            import hermes_cloud.app.touchpoints  # noqa: F401

            # Channel receivers — authenticated webhooks + URL verification.
            from hermes_cloud.app.channels.api import router as _channels_router
            import hermes_cloud.app.channels  # noqa: F401

            app.include_router(_gw_router)
            app.include_router(_trust_router)
            try:
                from hermes_cloud.app.memory.api import router as _memory_router
                app.include_router(_memory_router)
            except Exception:
                logger.exception("memory: router not mounted")
            # Per-user insight feed — powers the Copilot home "本周 AI
            # 表现" card.
            try:
                from hermes_cloud.app.insights.api import router as _insights_router
                app.include_router(_insights_router)
            except Exception:
                logger.exception("insights: router not mounted")
            app.include_router(_interactions_router)
            app.include_router(_touchpoints_router)
            app.include_router(_channels_router)

            # Phase 4.0c — agent_tasks list/done/cancel.
            try:
                from hermes_cloud.app.tasks.api import router as _tasks_router
                app.include_router(_tasks_router)
            except Exception:
                logger.exception("tasks: router not mounted")

            # Mail account CRUD (user-level IMAP + SMTP config).
            try:
                from hermes_cloud.app.mail.api import router as _mail_router
                app.include_router(_mail_router)
            except Exception:
                logger.exception("mail: router not mounted")

            # CRM Copilot — LLM tool-calling on one record (Phase B).
            # Replaces the frontend's regex-based intent parser.
            try:
                from hermes_cloud.app.copilot.routes import register_routes as _register_copilot
                _register_copilot(app)
            except Exception:
                logger.exception("copilot: router not mounted")

            # Phase 4 v2.2 §4.4 — channel push preferences API. Surfaces
            # ``user_channel_prefs`` (alembic 0049) so the SPA settings
            # page can manage feishu / iLink quiet hours, severity gate,
            # mute lists, etc.
            try:
                from hermes_cloud.app.channel.preferences_api import (
                    register_routes as _register_channel_prefs,
                )
                _register_channel_prefs(app)
            except Exception:
                logger.exception("channel_prefs: router not mounted")

            # Phase 5.6 — team audit dashboard API. 5 panels covering
            # AI behavior calendar / recommendation accuracy / sleeping
            # deals trend / member AI acceptance / pipeline funnel.
            try:
                from hermes_cloud.app.insights.team_audit_api import (
                    register_routes as _register_team_audit,
                )
                _register_team_audit(app)
            except Exception:
                logger.exception("team_audit: router not mounted")
            # /api/decisions/* SPA endpoints removed in consolidation
            # Phase 2.D (2026-05-16). See
            # docs/2026-05-16-decisions-coach-autodrive-consolidation.md.
            # Dashboards feature — /api/dashboards/* (spec
            # docs/superpowers/specs/2026-05-11-ai-dashboard-design.md).
            try:
                from hermes_cloud.app.dashboards.routes import (
                    register_routes as _register_dashboards,
                )
                _register_dashboards(app)
                # Build initial views + extended registry on boot. Wrap
                # in try so a startup glitch can't bring the whole app
                # down — first request will retry.
                try:
                    from hermes.crm.db import db_session
                    from hermes_cloud.app.dashboards.routes import (
                        force_refresh_registry as _refresh_dash_registry,
                    )
                    with db_session() as _db:
                        _refresh_dash_registry(_db)
                except Exception:
                    logger.exception("dashboards: initial schema refresh failed")
            except Exception:
                logger.exception("dashboards: router not mounted")
        except Exception as exc:  # pragma: no cover
            logger.warning("phase2: API routes not mounted (%s)", exc)

    return app
