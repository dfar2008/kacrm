"""``PgSessionDB`` — PG-backed implementation of hermes' SessionDB API.

Charter §九 single-DB compliance: hermes' upstream ``hermes_state.SessionDB``
is sqlite-only; this class is the drop-in PG replacement. Activated by
the patch-10 dispatch shim in ``hermes/hermes_state.py`` when
``HERMES_USE_CLOUD_DB=1``.

Schema lives in ``alembic/versions/0031_agent_sessions_messages.py`` and
mirrors the sqlite SCHEMA_VERSION=6 table layout, so the upstream
SessionDB test suite (137 tests) can be run against this class as the
regression net.

Design choices for the spike:

- Reuses cloud's ``crm.db`` connection pool so a single PG cluster
  serves both halves. No new pool, no new env var.
- Uses ``DbWrapper.fetchone/fetchall/execute/insert`` for the simple
  paths and dips into the underlying psycopg cursor for SET RETURNING
  + UPSERT cases.
- Keeps method signatures byte-identical to the upstream SessionDB so
  call sites in ``cli.py`` / ``run_agent.py`` / ``gateway/run.py`` /
  ``acp_adapter/session.py`` / ``mcp_serve.py`` work without edits.
- Methods that the spike doesn't need yet (export/prune/honcho hooks)
  raise ``NotImplementedError`` with a pointer to add them.

Not yet wired (follow-up commits in the patch-10 PR):

- The ``_conn`` attribute hack used by ``cli.py:2849`` and
  ``hermes_cli/web_server.py:831``. We promote those raw SQL sites to
  proper SessionDB methods before patch-10 lands.
- search_messages full FTS5-syntax compatibility — the spike uses
  ``websearch_to_tsquery`` which covers the AND/OR/quoted cases that
  the FTS5 sanitizer in upstream emits, but column-filter and NEAR
  syntax is not yet supported.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Charter §一定律 1 + 原语 ① — agent message → Interaction Log tap.
# ---------------------------------------------------------------------------
# Patch 09's tap lives on the sqlite ``_SqliteSessionDB.append_message``,
# which is bypassed when ``HERMES_USE_CLOUD_DB=1`` swaps in PgSessionDB
# (patch 10). So under the cloud-managed path the canonical
# ``interactions`` table never received ``agent.message`` rows, and the
# SPA endpoint ``/api/sessions/<id>/messages`` (which reads from
# ``interactions``) returned ``{"data": []}``.
#
# Fix: emit directly inside PgSessionDB.append_message. Same payload
# shape as the cloud HTTP endpoint /internal/agent/event so the SPA
# read path is unchanged.
def _emit_agent_message_interaction(
    *,
    session_id: str,
    msg_id: int,
    role: str,
    content: Optional[str],
    tool_calls: Any,
    tool_call_id: Optional[str],
    tool_name: Optional[str],
    reasoning: Optional[str],
    reasoning_details: Any,
    finish_reason: Optional[str],
    token_count: Optional[int],
    user_id: Optional[str],
    occurred_ts: float,
    display_kind: Optional[str] = None,
) -> None:
    """Emit a single agent.message row into ``interactions``.

    Failures are swallowed at WARNING level — the agent_messages row was
    already committed, so a hiccup here doesn't lose primary state.
    """
    # ``display_kind='hidden'`` rows are presentation-suppressed transcript
    # bookkeeping (compaction-summary carriers, interrupt scaffolds — upstream
    # #80622). Upstream clients (desktop/CLI) receive the flag and drop the
    # row; the interactions projection feeds surfaces (/api/search, legacy
    # message reads) that have no such filter, so suppress at the source —
    # otherwise a summary carrier surfaces as a giant "user" message.
    if display_kind == "hidden":
        return
    try:
        from hermes_cloud.app.interactions.emitter import emit as _emit
        import datetime as _dt

        # hermes v0.20 writes internal bookkeeping rows with roles outside
        # the conversational set (observed live: role='session_meta').
        # They belong in agent_messages (replay/state) but are NOT
        # conversation events — the interactions validator rightly rejects
        # them, which used to raise + traceback-log on EVERY such row.
        # Skip quietly instead.
        if role not in ("assistant", "user", "tool", "system"):
            logger.debug(
                "skipping interactions emit for non-conversational role %r "
                "(session %s)", role, session_id,
            )
            return

        profile_id = (os.environ.get("HERMES_CLOUD_PROFILE_ID", "").strip()
                      or "0")
        agent_name = (os.environ.get("HERMES_AGENT_NAME", "").strip()
                      or "hermes")

        actors: list[dict] = [{
            "type": "agent",
            "name": agent_name,
            "profile_id": profile_id,
        }]
        if user_id:
            try:
                actors.append({"type": "user", "id": int(user_id)})
            except (TypeError, ValueError):
                pass

        # Validator requires non-empty ``text`` (truthy or 0). For
        # tool-call-only assistant turns and tool result rows that carry
        # structured payloads in tool_calls/tool_name, fall back to a
        # short marker so the row still lands. The render layer keys on
        # tool_calls/tool_name, not text, in those cases.
        text_value = content if (content not in (None, "")) else "[no text]"
        body: Dict[str, Any] = {
            "session_id": str(session_id),
            "message_id": str(msg_id),
            "role": role,
            "text": text_value,
        }
        if tool_calls:
            body["tool_calls"] = tool_calls
        if tool_call_id:
            body["tool_call_id"] = tool_call_id
        if tool_name:
            body["tool_name"] = tool_name
        if reasoning:
            body["reasoning"] = reasoning
        if reasoning_details:
            body["reasoning_details"] = reasoning_details
        if finish_reason:
            body["finish_reason"] = finish_reason
        if display_kind:
            # Non-hidden bookkeeping kinds (model_switch, auto_continue, …)
            # pass through so renderers can demote/badge them like the
            # desktop does instead of showing a plain user/assistant bubble.
            body["display_kind"] = display_kind
        if isinstance(token_count, int):
            body["tokens"] = {"input": token_count}

        try:
            occurred_at = _dt.datetime.fromtimestamp(
                float(occurred_ts), tz=_dt.timezone.utc,
            )
        except (TypeError, ValueError, OverflowError):
            occurred_at = None

        _emit(
            kind="agent.message",
            actors=actors,
            subjects=[],
            content=body,
            source=f"agent:{profile_id}:{agent_name}",
            occurred_at=occurred_at,
        )
    except Exception as exc:  # noqa: BLE001 — swallow; primary write done
        logger.warning("agent_message_emit_failed: %s", exc, exc_info=True)


class _ConnFacade:
    """Thin shim that ``hermes_state.SessionDB._conn`` consumers call.

    A handful of upstream call sites (cli.py reopen path,
    hermes_cli/web_server.py analytics) reach into ``self._conn`` and
    run raw SQL. They use sqlite's ``?`` placeholder. Until those sites
    are promoted to proper methods, this facade translates ``?`` → ``%s``
    on each call and forwards to a fresh DbWrapper.

    This is intentionally limited: we expect to delete it in patch 10.
    """

    def __init__(self) -> None:
        from hermes.crm.db import db_session
        self._open = db_session

    # Table-name translation: upstream raw SQL says ``sessions`` /
    # ``messages``; PG schema names them ``agent_sessions`` /
    # ``agent_messages``. We do whole-word replace ONLY when not part
    # of a longer identifier (e.g. ``session_id`` should NOT become
    # ``agent_session_id``). Limited substitution scope: only the bare
    # table-reference forms we've audited.
    _TABLE_XLATE = [
        ("FROM sessions",        "FROM agent_sessions"),
        ("INTO sessions",        "INTO agent_sessions"),
        ("UPDATE sessions",      "UPDATE agent_sessions"),
        ("DELETE FROM sessions", "DELETE FROM agent_sessions"),
        ("JOIN sessions",        "JOIN agent_sessions"),
        ("FROM messages",        "FROM agent_messages"),
        ("INTO messages",        "INTO agent_messages"),
        ("UPDATE messages",      "UPDATE agent_messages"),
        ("DELETE FROM messages", "DELETE FROM agent_messages"),
        ("JOIN messages",        "JOIN agent_messages"),
    ]

    def execute(self, sql: str, params: tuple = ()) -> "_CursorFacade":
        # sqlite uses ?, psycopg uses %s. Naive replace is safe because
        # the call sites that hit this facade don't use ? for anything
        # but parameter placeholders (no LIKE patterns with ?).
        pg_sql = sql.replace("?", "%s")
        for src, dst in self._TABLE_XLATE:
            pg_sql = pg_sql.replace(src, dst)
        # Non-SELECT statements (UPDATE / DELETE / INSERT) must execute
        # eagerly: callers run them for the side effect, never call
        # fetchone/fetchall, and rely on a subsequent ``_conn.commit()``
        # which is a no-op in our facade. Lazy execution would mean the
        # UPDATE never actually runs.
        cur = _CursorFacade(self._open, pg_sql, params)
        sql_norm = pg_sql.strip().lstrip("(").lstrip().lower()
        if not sql_norm.startswith("select"):
            cur._ensure_executed()
        return cur

    def commit(self) -> None:
        # Each _CursorFacade.execute opens a session_scope which auto-
        # commits on __exit__; explicit commit is a no-op.
        return


class _CursorFacade:
    def __init__(self, opener, sql: str, params: tuple) -> None:
        self._opener = opener
        self._sql = sql
        self._params = params
        self._rows: Optional[list] = None
        self._executed = False

    def _ensure_executed(self) -> None:
        if self._executed:
            return
        with self._opener() as db:
            sql_norm = self._sql.strip().lstrip("(").lstrip().lower()
            if sql_norm.startswith("select"):
                self._rows = db.fetchall(self._sql, self._params) or []
            else:
                db.execute(self._sql, self._params)
                self._rows = []
        self._executed = True

    def fetchone(self) -> Any:
        self._ensure_executed()
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        self._ensure_executed()
        return list(self._rows or [])


class PgSessionDB:
    """PG-backed SessionDB. See module docstring for design notes."""

    # API parity with sqlite ``SessionDB``. The upstream test suite
    # accesses these as class attributes; missing them caused ~40 of
    # the 137 spike test failures.
    MAX_TITLE_LENGTH = 100
    _WRITE_MAX_RETRIES = 15  # cosmetic: PG doesn't WAL-lock-spin, but
    _WRITE_RETRY_MIN_S = 0.020  # tests assert these exist as class-level
    _WRITE_RETRY_MAX_S = 0.150  # constants for parity.
    _CHECKPOINT_EVERY_N_WRITES = 50

    # Shared pre-call sequence repair imports the dispatched SessionDB class,
    # not an instance: expose the same codecs used by PG writes and reloads.
    # Upstream repair refuses to merge malformed SQLite sentinel strings.
    # Keep its live marker here; PG's private storage prefix is below.
    _CONTENT_JSON_PREFIX = "\x00json:"

    @staticmethod
    def _encode_content(content: Any) -> Any:
        return _encode_content(content)

    @staticmethod
    def _decode_content(content: Any) -> Any:
        return _decode_content(content)

    @staticmethod
    def _encode_display_metadata(metadata: Any) -> Optional[str]:
        return _encode_display_metadata(metadata)

    @staticmethod
    def _decode_display_metadata(metadata: Any) -> Optional[Dict[str, Any]]:
        return _decode_display_metadata(metadata)

    @staticmethod
    def _is_fts_write_corruption_error(exc: Exception) -> bool:
        # gateway.session_transcript consults this on SQLite exceptions even
        # when the facade dispatches to PG; preserve its provenance gate.
        from hermes_state_errors import is_fts_scoped_corruption_error
        return is_fts_scoped_corruption_error(exc)

    def __init__(self, db_path: Any = None) -> None:
        # db_path is accepted for API compat but ignored — PgSessionDB
        # uses cloud's crm.db pool.
        self._lock = threading.Lock()
        self._conn = _ConnFacade()

    def close(self) -> None:
        # Cloud's crm.db pool is process-wide; close is a no-op here so
        # multiple SessionDB instances share the pool cleanly.
        return

    # =========================================================================
    # Sessions
    # =========================================================================

    def create_session(
        self,
        session_id: str,
        source: str,
        model: str = None,
        model_config: Dict[str, Any] = None,
        system_prompt: str = None,
        user_id: str = None,
        parent_session_id: str = None,
        **kwargs: Any,
    ) -> str:
        # ``**kwargs`` absorbs forward-compatible params the upstream hermes
        # SessionDB protocol adds over time (e.g. v2026.6.19's ``cwd``) that
        # this PG override does not persist — mirrors upstream
        # ``_SqliteSessionDB.create_session(..., **kwargs)``. Without it an
        # upgrade silently breaks session persistence: the caller passes the
        # new kwarg, this method raises TypeError, the row is never written.
        from hermes.crm.db import db_session

        # Charter §一原语 ② Memory Substrate cross-channel metadata.
        # Upstream's gateway session_context contextvar carries
        # chat_name + thread_id when create_session fires under a
        # gateway request; pull them in best-effort so memory queries
        # like "all sessions in QA group chat" can filter without a
        # JSONB extract. Outside a gateway context (e.g. CLI startup)
        # these stay NULL — additive columns from alembic 0034.
        chat_name = thread_id = None
        try:
            from gateway.session_context import get_session_env  # type: ignore[import-not-found]
            chat_name = get_session_env("HERMES_SESSION_CHAT_NAME", "") or None
            thread_id = get_session_env("HERMES_SESSION_THREAD_ID", "") or None
        except Exception:
            # Not running under a hermes gateway — non-cloud pathways
            # (test, CLI, ad-hoc invocation) leave these NULL.
            pass

        # Charter v2 §定律 4 — make agent_sessions.user_id the single
        # source of truth for ChatInbox attribution. Two enrichments:
        #
        #   1. api_server path (chatbox/copilot/decisions/dashboards/TUI)
        #      passes user_id=None. Cloud's hermes patch 38 binds the
        #      X-Hermes-Cloud-User-Id header to SESSION_USER_ID
        #      ContextVar; read it here.
        #
        #   2. weixin/wecom/feishu paths pass user_id=<external openid>.
        #      Under the B2 per-user-bot model the bot belongs to this
        #      profile's owner, so every IM-originated session is that
        #      owner's — overwrite the sender openid with this
        #      subprocess's own cloud user_id (CRM_USER_ID, set by
        #      ProfileManager at spawn).
        if not user_id:
            try:
                from gateway.session_context import get_session_env  # type: ignore[import-not-found]
                user_id = get_session_env("HERMES_SESSION_USER_ID", "") or None
            except Exception:
                pass
        if user_id and source in ("weixin", "wecom", "wecom_ilink", "feishu", "lark", "email"):
            # ``agent.secret_scope`` resolves as a top-level module in the
            # gateway subprocess (PYTHONPATH=hermes) and as ``hermes.agent.*``
            # in the cloud process — try both (mirrors identity.py's shim).
            try:  # pragma: no cover - import-shim
                from agent.secret_scope import is_multiplex_active
            except ImportError:  # pragma: no cover
                from hermes.agent.secret_scope import is_multiplex_active

            if is_multiplex_active():
                # Multiplex is a SINGLE gateway process serving MANY tenants,
                # so it deliberately does NOT set a process-level CRM_USER_ID
                # (identity is resolved per turn). Reading os.environ here would
                # either miss — dropping the session onto the raw external
                # openid, invisible under list_sessions' exact user_id match —
                # or, if the env leaked into the mux, collapse every tenant's IM
                # sessions onto one user_id (cross-tenant attribution leak).
                # Resolve from the active per-turn profile scope instead, the
                # same source crm_tools._resolve_identity uses. current_user_id_int
                # is per-turn + fail-closed: with no scope installed it raises
                # UnscopedIdentityError rather than attributing to a default
                # tenant. Let that propagate — do NOT collapse to a default.
                from .identity import current_user_id_int

                user_id = str(current_user_id_int())
            else:
                # Non-multiplex (single-profile) path — byte-for-byte unchanged.
                # CRM_USER_ID is injected into the profile subprocess by
                # ProfileManager at spawn.
                crm_uid = os.environ.get("CRM_USER_ID", "").strip()
                if crm_uid:
                    user_id = crm_uid
                else:
                    # An empty value here means a spawn-wiring bug. Without it
                    # the session stays attributed to the raw external openid
                    # and, since list_sessions now does an exact user_id match,
                    # becomes invisible in the inbox. Surface it loudly rather
                    # than dropping it silently.
                    logger.warning(
                        "IM session source=%s but CRM_USER_ID is unset — "
                        "attributing to raw external id %r; session may be "
                        "invisible in the inbox. Check ProfileManager spawn env.",
                        source, user_id,
                    )

        with db_session() as db:
            db.execute(
                """
                INSERT INTO agent_sessions
                  (id, source, user_id, model, model_config, system_prompt,
                   parent_session_id, started_at,
                   chat_name, thread_id)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    session_id, source, user_id, model,
                    json.dumps(model_config) if model_config else None,
                    system_prompt, parent_session_id, time.time(),
                    chat_name, thread_id,
                ),
            )
        return session_id

    def queue_token_counts(self, session_id: str, **kwargs: Any) -> None:
        """hermes v0.20 async token-accounting API — applied synchronously.

        Upstream's sqlite store queues deltas for a background writer because
        a cold state.db UPDATE stalls the turn thread; a PG UPDATE through
        pgbouncer has no such cliff, so we apply the delta inline with
        identical semantics. Without this method every API call's token/cost
        delta raised AttributeError (caught upstream) and cloud sessions
        stopped accumulating usage on v0.20.
        """
        self.update_token_counts(session_id, **kwargs)

    def flush_token_counts(self, timeout: float = 5.0) -> bool:
        """v0.20 turn-finalize barrier for the sqlite token queue.

        PG applies deltas synchronously (see queue_token_counts) — there is
        never anything queued, so the barrier is trivially satisfied.
        """
        return True

    def publish_compression_child(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
        source: str,
        messages: Any = None,
        model: str = None,
        model_config: Dict[str, Any] = None,
        system_prompt: str = None,
        cwd: str = None,
        profile_name: str = None,
        compression_lock_holder: str = None,
        require_compression_lease: bool = True,
        **kwargs: Any,
    ) -> None:
        """hermes v0.20 compression rotation: close parent, publish child.

        Mirrors upstream ``_SqliteSessionDB.publish_compression_child``:
        child session row (parented, inheriting user/routing identity) +
        compacted handoff messages + parent ``end_reason='compression'``.
        Composed from the existing PG primitives (create_session /
        append_messages_batch / end_session), so it shares their encoding
        paths. Divergences, accepted deliberately: not one transaction
        (same trade-off append_messages_batch documents), and the sqlite
        compression-lease check is skipped — the lease lives in sqlite's
        ``compression_locks`` table, and on PG the per-profile hermes
        process is the only compressor for its sessions, so the lease's
        multi-writer race doesn't exist here.
        """
        from hermes.crm.db import db_session

        parent_user_id = None
        with db_session() as db:
            row = db.fetchone(
                "SELECT user_id, ended_at FROM agent_sessions WHERE id = %s",
                (parent_session_id,),
            )
        if row is None:
            raise RuntimeError(f"Compression parent not found: {parent_session_id}")
        if row.get("ended_at") is not None:
            raise RuntimeError(f"Compression parent already ended: {parent_session_id}")
        parent_user_id = row.get("user_id")

        self.create_session(
            session_id=child_session_id,
            source=source,
            model=model,
            model_config=model_config,
            system_prompt=system_prompt,
            user_id=parent_user_id,
            parent_session_id=parent_session_id,
            cwd=cwd,
        )
        if messages:
            self.append_messages_batch(
                session_id=child_session_id,
                messages=messages,
                compression_lock_holder=compression_lock_holder,
            )
        self.end_session(parent_session_id, "compression")

    def get_session_title_source(self, session_id: str) -> Optional[str]:
        """v0.20 title provenance — PG has no title_source column yet.

        Returning None means an inherited title is re-stamped by its setter's
        authority on compression rotation (an auto-title may lose
        upgradeability). Graceful-degradation stub; the call sites
        try/except around it either way.
        """
        return None

    def set_session_title_source(self, session_id: str, source: str) -> bool:
        """v0.20 title provenance setter — accepted and dropped (no column)."""
        return False

    def end_session(self, session_id: str, end_reason: str) -> None:
        """Mirror upstream v0.11.0: first end_reason wins. A subsequent
        ``end_session()`` against an already-ended session is a no-op so
        compression-split sessions keep their ``end_reason='compression'``
        even if a stale CLI fires a re-end with a different reason."""
        from hermes.crm.db import db_session
        with db_session() as db:
            db.execute(
                "UPDATE agent_sessions SET ended_at = %s, end_reason = %s "
                "WHERE id = %s AND ended_at IS NULL",
                (time.time(), end_reason, session_id),
            )

    def reopen_session(self, session_id: str) -> None:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.execute(
                "UPDATE agent_sessions SET ended_at = NULL, "
                "end_reason = NULL WHERE id = %s",
                (session_id,),
            )

    def update_system_prompt(self, session_id: str, system_prompt: str) -> None:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.execute(
                "UPDATE agent_sessions SET system_prompt = %s WHERE id = %s",
                (system_prompt, session_id),
            )

    def update_token_counts(
        self,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        estimated_cost_usd: Optional[float] = None,
        actual_cost_usd: Optional[float] = None,
        cost_status: Optional[str] = None,
        cost_source: Optional[str] = None,
        pricing_version: Optional[str] = None,
        billing_provider: Optional[str] = None,
        billing_base_url: Optional[str] = None,
        billing_mode: Optional[str] = None,
        api_call_count: int = 0,
        absolute: bool = False,
        source: Optional[str] = None,
        task: str = "",
    ) -> None:
        from hermes.crm.db import db_session
        # Explicit casts on NULL-able numeric/text params: psycopg can't
        # infer the type when the Python value is None, and PG complains
        # ``could not determine data type of parameter $N``. Numerics
        # need ::numeric, free-form strings need ::text.
        if absolute:
            sql = """
                UPDATE agent_sessions SET
                    input_tokens       = %s,
                    output_tokens      = %s,
                    cache_read_tokens  = %s,
                    cache_write_tokens = %s,
                    reasoning_tokens   = %s,
                    estimated_cost_usd = COALESCE(%s::numeric, 0),
                    actual_cost_usd    = CASE WHEN %s::numeric IS NULL
                                              THEN actual_cost_usd ELSE %s::numeric END,
                    cost_status        = COALESCE(%s::text, cost_status),
                    cost_source        = COALESCE(%s::text, cost_source),
                    pricing_version    = COALESCE(%s::text, pricing_version),
                    billing_provider   = COALESCE(billing_provider, %s::text),
                    billing_base_url   = COALESCE(billing_base_url, %s::text),
                    billing_mode       = COALESCE(billing_mode, %s::text),
                    api_call_count     = %s,
                    model              = COALESCE(model, %s::text)
                WHERE id = %s
            """
        else:
            sql = """
                UPDATE agent_sessions SET
                    input_tokens       = input_tokens + %s,
                    output_tokens      = output_tokens + %s,
                    cache_read_tokens  = cache_read_tokens + %s,
                    cache_write_tokens = cache_write_tokens + %s,
                    reasoning_tokens   = reasoning_tokens + %s,
                    estimated_cost_usd = COALESCE(estimated_cost_usd, 0) + COALESCE(%s::numeric, 0),
                    actual_cost_usd    = CASE WHEN %s::numeric IS NULL
                                              THEN actual_cost_usd
                                              ELSE COALESCE(actual_cost_usd, 0) + %s::numeric END,
                    cost_status        = COALESCE(%s::text, cost_status),
                    cost_source        = COALESCE(%s::text, cost_source),
                    pricing_version    = COALESCE(%s::text, pricing_version),
                    billing_provider   = COALESCE(billing_provider, %s::text),
                    billing_base_url   = COALESCE(billing_base_url, %s::text),
                    billing_mode       = COALESCE(billing_mode, %s::text),
                    api_call_count     = COALESCE(api_call_count, 0) + %s,
                    model              = COALESCE(model, %s::text)
                WHERE id = %s
            """
        has_usage = any((input_tokens, output_tokens, cache_read_tokens,
                         cache_write_tokens, reasoning_tokens, api_call_count, estimated_cost_usd))
        has_accounted_usage = has_usage or bool(actual_cost_usd)
        main_route = has_accounted_usage and not task
        params = (
            input_tokens, output_tokens, cache_read_tokens,
            cache_write_tokens, reasoning_tokens,
            estimated_cost_usd,
            actual_cost_usd, actual_cost_usd,
            cost_status, cost_source, pricing_version,
            billing_provider if main_route else None,
            billing_base_url if main_route else None,
            billing_mode if main_route else None,
            api_call_count,
            model if main_route else None, session_id,
        )
        with db_session() as db, db.transaction():
            db.execute("INSERT INTO agent_sessions (id, source, started_at) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING", (session_id, source or "unknown", time.time()))
            if main_route and model and billing_provider:
                # The first successful fallback call owns the coherent aggregate
                # route; later switches live in the per-call history below.
                db.execute("""UPDATE agent_sessions SET model = %s, billing_provider = %s,
                    billing_base_url = %s, billing_mode = %s
                    WHERE id = %s AND COALESCE(api_call_count, 0) = 0""",
                    (model, billing_provider, billing_base_url, billing_mode, session_id))
            db.execute(sql, params)
            if not absolute and has_usage:
                db.execute("""INSERT INTO agent_session_model_usage
                    (session_id, model, billing_provider, billing_base_url, billing_mode, task, api_call_count, last_seen)
                    SELECT id, COALESCE(%s, CASE WHEN %s = '' THEN model END, 'unknown'),
                           COALESCE(%s, CASE WHEN %s = '' THEN billing_provider END, ''),
                           COALESCE(%s, CASE WHEN %s = '' THEN billing_base_url END, ''),
                           COALESCE(%s, CASE WHEN %s = '' THEN billing_mode END, ''), %s, %s, %s
                      FROM agent_sessions WHERE id = %s""",
                    (model, task, billing_provider, task, billing_base_url, task, billing_mode, task, task, api_call_count, time.time(), session_id))

    def get_recent_session_model_route(self, session_id: str) -> Optional[Dict[str, Any]]:
        from hermes.crm.db import db_session
        self.flush_token_counts()
        with db_session() as db:
            return db.fetchone("""SELECT model, billing_provider, billing_base_url, billing_mode, api_call_count
                FROM agent_session_model_usage WHERE session_id = %s AND task = ''
                AND model <> 'unknown' AND billing_provider <> '' ORDER BY last_seen DESC, id DESC LIMIT 1""", (session_id,))

    def get_dominant_session_model_route(self, session_id: str) -> Optional[Dict[str, Any]]:
        from hermes.crm.db import db_session
        self.flush_token_counts()
        with db_session() as db:
            return db.fetchone("""WITH dominant AS (
                SELECT model, billing_provider, SUM(api_call_count) AS api_call_count
                FROM agent_session_model_usage WHERE session_id = %s AND task = ''
                  AND model <> 'unknown' AND billing_provider <> ''
                GROUP BY model, billing_provider
                ORDER BY SUM(api_call_count) DESC, MAX(last_seen) DESC, MAX(id) DESC LIMIT 1)
                SELECT u.model, u.billing_provider, u.billing_base_url, u.billing_mode,
                       d.api_call_count
                FROM agent_session_model_usage u JOIN dominant d
                  ON u.model = d.model AND u.billing_provider = d.billing_provider
                WHERE u.session_id = %s AND u.task = ''
                ORDER BY u.last_seen DESC, u.id DESC LIMIT 1""", (session_id, session_id))

    def get_message_role(self, session_id: str, row_id: int) -> Optional[str]:
        if not session_id:
            return None
        from hermes.crm.db import db_session
        with db_session() as db:
            row = db.fetchone("SELECT role FROM agent_messages WHERE session_id = %s AND id = %s", (session_id, int(row_id)))
        return row["role"] if row else None

    def update_session_cwd(self, session_id: str, cwd: str, git_branch: Optional[str] = None,
                           git_repo_root: Optional[str] = None, replace_git_meta: bool = False) -> Optional[int]:
        if not session_id or not cwd:
            return None
        from hermes.crm.db import db_session
        branch, root = (git_branch or "").strip() or None, (git_repo_root or "").strip() or None
        with db_session() as db:
            row = db.fetchone("""UPDATE agent_sessions SET
                git_branch = CASE WHEN cwd IS DISTINCT FROM %s OR %s THEN %s ELSE COALESCE(%s, git_branch) END,
                git_repo_root = CASE WHEN cwd IS DISTINCT FROM %s OR %s THEN %s ELSE COALESCE(%s, git_repo_root) END,
                cwd = %s, git_metadata_generation = COALESCE(git_metadata_generation, 0) + 1
                WHERE id = %s RETURNING git_metadata_generation""",
                (cwd, replace_git_meta, branch, branch, cwd, replace_git_meta, root, root, cwd, session_id))
        return int(row["git_metadata_generation"]) if row else None

    def ensure_session(
        self, session_id: str, source: str = "unknown", model: str = None,
    ) -> None:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.execute(
                """
                INSERT INTO agent_sessions (id, source, model, started_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (session_id, source, model, time.time()),
            )

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        from hermes.crm.db import db_session
        with db_session() as db:
            row = db.fetchone(
                "SELECT * FROM agent_sessions WHERE id = %s", (session_id,),
            )
        if not row:
            return None
        # model_config is stored as jsonb; expose as dict to match sqlite path
        # which stores it as json string but the upstream caller expects
        # parsed values. Tests commonly compare dicts directly.
        return _normalize_session_row(row)

    def resolve_session_id(
        self, session_id_or_prefix: str,
    ) -> Optional[str]:
        from hermes.crm.db import db_session
        with db_session() as db:
            # Exact match first.
            row = db.fetchone(
                "SELECT id FROM agent_sessions WHERE id = %s",
                (session_id_or_prefix,),
            )
            if row:
                return row["id"]
            # Prefix match — return only if unambiguous. Escape LIKE
            # wildcards in the input so a literal underscore in a
            # session id (e.g. ``20260315_092437``) doesn't match
            # arbitrary characters.
            escaped = (
                session_id_or_prefix
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            rows = db.fetchall(
                "SELECT id FROM agent_sessions "
                "WHERE id LIKE %s ESCAPE %s LIMIT 2",
                (escaped + "%", "\\"),
            ) or []
            if len(rows) == 1:
                return rows[0]["id"]
            return None

    # =========================================================================
    # Titles
    # =========================================================================

    @staticmethod
    def sanitize_title(title: Optional[str]) -> Optional[str]:
        # Reuse the upstream sqlite class directly — pure string logic,
        # no need to re-implement. Bypass the SessionDB symbol because
        # in PG mode that's us, which would recurse infinitely.
        from hermes_state import _SqliteSessionDB
        return _SqliteSessionDB.sanitize_title(title)

    def set_session_title(self, session_id: str, title: str) -> bool:
        """Mirror upstream contract:
        - sanitize_title raises ValueError on too-long titles
        - duplicate title in use by another session → raise ValueError
        - empty / whitespace title → clears (sets NULL)
        - returns True iff the session row existed.
        """
        clean = self.sanitize_title(title)  # may raise ValueError on length
        from psycopg import errors as _pg_errors
        from hermes.crm.db import db_session
        with db_session() as db:
            if not clean:
                # Clearing the title — the uniqueness constraint is a
                # partial index on (title) WHERE title IS NOT NULL, so
                # NULLs never collide. Plain UPDATE.
                row = db.fetchone(
                    "UPDATE agent_sessions SET title = NULL WHERE id = %s "
                    "RETURNING id",
                    (session_id,),
                )
                return row is not None

            # Atomic claim: the UPDATE fires only if no *other* session
            # already holds this title. Folding the uniqueness check into
            # the WHERE clause closes the check-then-act race a separate
            # SELECT + UPDATE leaves open — two concurrent callers can no
            # longer both pass an independent check and then both write.
            # The partial unique index idx_agent_sessions_title_unique is
            # the backstop for the residual READ COMMITTED window (two txns
            # whose NOT EXISTS both pass before either commits).
            try:
                row = db.fetchone(
                    "UPDATE agent_sessions SET title = %s "
                    "WHERE id = %s "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM agent_sessions "
                    "    WHERE title = %s AND id <> %s) "
                    "RETURNING id",
                    (clean, session_id, clean, session_id),
                )
            except _pg_errors.UniqueViolation as exc:
                raise ValueError(
                    f"Title '{clean}' is already in use by another session"
                ) from exc
            if row is not None:
                return True

            # No row updated: either the session row doesn't exist, or the
            # NOT EXISTS guard tripped because another session holds the
            # title. Disambiguate to preserve the upstream contract.
            owner = db.fetchone(
                "SELECT id FROM agent_sessions WHERE title = %s AND id <> %s",
                (clean, session_id),
            )
            if owner is not None:
                raise ValueError(
                    f"Title '{clean}' is already in use by "
                    f"session {owner['id']}"
                )
            return False  # session row does not exist

    def get_session_title(self, session_id: str) -> Optional[str]:
        from hermes.crm.db import db_session
        with db_session() as db:
            row = db.fetchone(
                "SELECT title FROM agent_sessions WHERE id = %s",
                (session_id,),
            )
        return row["title"] if row and row.get("title") else None

    def get_session_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        from hermes.crm.db import db_session
        with db_session() as db:
            row = db.fetchone(
                "SELECT * FROM agent_sessions WHERE title = %s", (title,),
            )
        return _normalize_session_row(row) if row else None

    def resolve_session_by_title(self, title: str) -> Optional[str]:
        """Mirror upstream: prefer the latest numbered variant in a lineage,
        else exact match. ``"my project"`` resolves to ``"my project #3"``
        if present."""
        exact = self.get_session_by_title(title)
        escaped = (
            title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        from hermes.crm.db import db_session
        with db_session() as db:
            rows = db.fetchall(
                "SELECT id FROM agent_sessions "
                "WHERE title LIKE %s ESCAPE %s "
                "ORDER BY started_at DESC LIMIT 1",
                (f"{escaped} #%", "\\"),
            ) or []
        if rows:
            return rows[0]["id"]
        return exact["id"] if exact else None

    def get_next_title_in_lineage(self, base_title: str) -> str:
        """Mirror upstream lineage suffix format ``" #N"``.

        Examples (matching upstream sqlite SessionDB):
          ("my project")          → "my project #2" if "my project" exists
          ("my project #2")       → "my project #3" if "#2" exists
          ("my project #4")       → strips suffix, base="my project", finds
                                    highest #N, returns next.
        """
        import re as _re
        # Strip existing " #N" suffix to find true base.
        m = _re.match(r"^(.*?) #(\d+)$", base_title)
        base = m.group(1) if m else base_title

        from hermes.crm.db import db_session
        # Escape PG LIKE wildcards in the base.
        escaped = (
            base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        with db_session() as db:
            rows = db.fetchall(
                "SELECT title FROM agent_sessions "
                "WHERE title = %s OR title LIKE %s ESCAPE %s",
                (base, escaped + " #%", "\\"),
            ) or []
        max_n = 1  # base itself counts as #1 if it exists
        base_exists = False
        for r in rows:
            t = r.get("title") or ""
            if t == base:
                base_exists = True
            elif t.startswith(base + " #"):
                suffix = t[len(base) + 2:]
                if suffix.isdigit():
                    max_n = max(max_n, int(suffix))
        if not base_exists and max_n == 1:
            return base
        return f"{base} #{max_n + 1}"

    # =========================================================================
    # List
    # =========================================================================

    def list_sessions_rich(
        self,
        source: str = None,
        exclude_sources: List[str] = None,
        limit: int = 20,
        offset: int = 0,
        include_children: bool = False,
    ) -> List[Dict[str, Any]]:
        """Mirror upstream signature + preview/last_active semantics.

        Preview = first 60 chars of first user message, with newlines
        collapsed to spaces; trailing "..." appended if truncated.
        last_active = MAX(timestamp) of any message, or s.started_at
        when no messages exist.
        """
        from hermes.crm.db import db_session
        clauses = []
        args: list = []
        if not include_children:
            clauses.append("s.parent_session_id IS NULL")
        if source:
            clauses.append("s.source = %s")
            args.append(source)
        if exclude_sources:
            placeholders = ", ".join(["%s"] * len(exclude_sources))
            clauses.append(f"s.source NOT IN ({placeholders})")
            args.extend(exclude_sources)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.extend([int(limit), int(offset)])
        with db_session() as db:
            rows = db.fetchall(
                f"""
                SELECT s.*,
                  COALESCE(
                    (SELECT substr(
                              regexp_replace(m.content, '[\r\n]+', ' ', 'g'),
                              1, 63)
                       FROM agent_messages m
                      WHERE m.session_id = s.id
                        AND m.role = 'user'
                        AND m.content IS NOT NULL
                      ORDER BY m.timestamp ASC, m.id ASC
                      LIMIT 1),
                    ''
                  ) AS _preview_raw,
                  COALESCE(
                    (SELECT MAX(m2.timestamp) FROM agent_messages m2
                      WHERE m2.session_id = s.id),
                    s.started_at
                  ) AS last_active
                FROM agent_sessions s
                {where}
                ORDER BY s.started_at DESC
                LIMIT %s OFFSET %s
                """,
                tuple(args),
            ) or []
        sessions = []
        for r in rows:
            s = dict(r)
            raw = (s.pop("_preview_raw", "") or "").strip()
            if raw:
                text = raw[:60]
                s["preview"] = text + ("..." if len(raw) > 60 else "")
            else:
                s["preview"] = ""
            sessions.append(s)
        return sessions

    # =========================================================================
    # Messages
    # =========================================================================

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str = None,
        tool_name: str = None,
        tool_calls: Any = None,
        tool_call_id: str = None,
        token_count: int = None,
        finish_reason: str = None,
        reasoning: str = None,
        reasoning_content: str = None,
        reasoning_details: Any = None,
        codex_reasoning_items: Any = None,
        codex_message_items: Any = None,
        platform_message_id: str = None,
        observed: bool = False,
        timestamp: Any = None,
        api_content: Optional[str] = None,
        display_kind: Optional[str] = None,
        display_metadata: Any = None,
        duration_ms: int = None,
        turn_meta: Any = None,
        message_uid: Optional[str] = None,
        absorbed_message_uids: Any = None,
        tool_call_uids: Any = None,
        tool_call_uid: Optional[str] = None,
        effect_disposition: Optional[str] = None,
        **kwargs: Any,
    ) -> int:
        batch_db = kwargs.pop("_batch_db", None)
        absorbed_message_uids = kwargs.pop("_absorbed_message_uids", absorbed_message_uids)
        tool_call_uids = kwargs.pop("_tool_call_uids", tool_call_uids)
        tool_call_uid = kwargs.pop("_tool_call_uid", tool_call_uid)
        identity = _message_identity(dict(
            role=role, tool_calls=tool_calls, message_uid=message_uid,
            absorbed_message_uids=absorbed_message_uids,
            tool_call_uids=tool_call_uids, tool_call_uid=tool_call_uid,
        ))
        # ``platform_message_id`` / ``observed`` / ``timestamp`` were added by
        # upstream v2026.6.19 and ARE passed by the live gateway (session.py).
        # They were previously swallowed by ``**kwargs`` and lost — most
        # importantly ``observed`` (group-chat spectator messages the agent must
        # withhold from replayable history; losing it leaks that context back
        # into replay and can corrupt the assistant→tool sequence). Accept them
        # explicitly and persist (columns added in alembic 0110). ``**kwargs``
        # stays for the next forward-compatible param.
        #
        # ``api_content`` / ``display_kind`` / ``display_metadata`` are the
        # v0.20 SessionState per-message display fields (columns added in
        # alembic 0114), passed by run_agent's turn-boundary batch flush.
        # ``display_kind='hidden'`` marks compaction-summary carrier rows —
        # upstream #80622: losing it makes a reloaded summary carrier
        # re-occupy the active user slot and render as a real user message.
        # ``api_content`` is the byte-fidelity "exact bytes sent to the API"
        # sidecar; losing it breaks prompt-cache-stable replay after reload.
        if kwargs:
            # A param reached us that neither the explicit signature nor the
            # PG columns know about — almost always an upstream hermes upgrade
            # adding a new append_message field. We absorb it (message still
            # persists) but surface it LOUDLY so the drift is noticed and the
            # data deliberately wired in, instead of silently lost. The
            # signature contract is guarded by
            # tests/hermes_cloud/test_session_db_contract.py.
            logger.warning(
                "PgSessionDB.append_message ignoring unpersisted param(s) %s "
                "— likely a new upstream field; add a column + persistence if "
                "the SPA needs it.",
                sorted(kwargs),
            )
        from hermes.crm.db import db_session
        # Mirror upstream's explicit-timestamp coercion (hermes_state.append_message):
        # honor a caller-supplied timestamp (datetime or epoch float) for
        # import/backfill/replay; default to now otherwise.
        message_timestamp = time.time()
        if timestamp is not None:
            try:
                if hasattr(timestamp, "timestamp"):
                    message_timestamp = float(timestamp.timestamp())
                else:
                    message_timestamp = float(timestamp)
            except (TypeError, ValueError):
                logger.debug("Ignoring invalid explicit message timestamp: %r", timestamp)
        tool_calls_json = (
            json.dumps(tool_calls) if tool_calls else None
        )
        reasoning_details_json = (
            json.dumps(reasoning_details) if reasoning_details else None
        )
        codex_items_json = (
            json.dumps(codex_reasoning_items)
            if codex_reasoning_items else None
        )
        codex_msg_items_json = (
            json.dumps(codex_message_items)
            if codex_message_items else None
        )
        turn_meta_json = json.dumps(turn_meta) if turn_meta is not None else None
        display_metadata_json = _encode_display_metadata(display_metadata)
        # Hermes v11 (2026-05-07) lets append_message receive ``content``
        # as a list-of-parts (multimodal: ``[{"type":"text","text":...},
        # {"type":"image_url", ...}]``) or a dict for codex-format turns.
        # sqlite uses a sentinel-prefix JSON encoding (see SessionDB.
        # _encode_content / _decode_content); we mirror it here so a row
        # written under sqlite and read under PG (or vice versa) round-trips
        # identically. Plain strings pass through unchanged.
        # Keep the original around — the interactions emit below MUST get
        # the un-encoded content so SPA renderers don't see sentinel garbage
        # for multimodal turns.
        original_content = content
        content = _encode_content(content)
        num_tool_calls = (
            len(tool_calls) if isinstance(tool_calls, list)
            else (1 if tool_calls is not None else 0)
        )
        from contextlib import nullcontext
        with (nullcontext(batch_db) if batch_db is not None else db_session()) as db:
            row = db.fetchone(
                """
                INSERT INTO agent_messages
                  (session_id, role, content, tool_call_id, tool_calls,
                   tool_name, timestamp, token_count, finish_reason,
                   reasoning, reasoning_content, reasoning_details,
                   codex_reasoning_items, codex_message_items,
                   platform_message_id, observed,
                   api_content, display_kind, display_metadata,
                   duration_ms, turn_meta, message_uid, absorbed_message_uids,
                   tool_call_uids, tool_call_uid, effect_disposition)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                        %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb,
                        %s, %s::jsonb, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (
                    session_id, role, content, tool_call_id,
                    tool_calls_json, tool_name, message_timestamp,
                    token_count, finish_reason, reasoning,
                    reasoning_content,
                    reasoning_details_json, codex_items_json,
                    codex_msg_items_json,
                    platform_message_id, (1 if observed else 0),
                    # Mirror sqlite: non-str values are dropped, not coerced.
                    api_content if isinstance(api_content, str) else None,
                    display_kind if isinstance(display_kind, str) else None,
                    display_metadata_json,
                    duration_ms, turn_meta_json, *identity, effect_disposition,
                ),
            )
            msg_id = int(row["id"]) if row else 0
            if num_tool_calls > 0:
                db.execute(
                    "UPDATE agent_sessions SET "
                    "  message_count   = message_count + 1, "
                    "  tool_call_count = tool_call_count + %s "
                    "WHERE id = %s",
                    (num_tool_calls, session_id),
                )
            else:
                db.execute(
                    "UPDATE agent_sessions SET "
                    "  message_count = message_count + 1 "
                    "WHERE id = %s",
                    (session_id,),
                )
            # Look up user_id while we still hold the cursor so the
            # interactions actor list can name the human side.
            sess_row = db.fetchone(
                "SELECT user_id FROM agent_sessions WHERE id = %s",
                (session_id,),
            )
            sess_user_id = (sess_row.get("user_id") if sess_row else None)
            # pylint: disable=protected-access
            if batch_db is None:
                db._maybe_commit(True)

        # Charter §一原语 ① — fan out to interactions so SPA's
        # ``/api/sessions/<id>/messages`` read path (which queries
        # ``interactions WHERE kind='agent.message'``) sees the row.
        # Same connection-pool, same transaction-end semantics; emit
        # is best-effort so a logged warning here can't roll back the
        # primary agent_messages write above.
        event = dict(
            session_id=session_id,
            msg_id=msg_id,
            role=role,
            content=original_content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            reasoning=reasoning,
            reasoning_details=reasoning_details,
            finish_reason=finish_reason,
            token_count=token_count,
            user_id=sess_user_id,
            occurred_ts=time.time(),
            display_kind=display_kind if isinstance(display_kind, str) else None,
        )
        if batch_db is None:
            _emit_agent_message_interaction(**event)
        else:
            batch_db.after_commit(lambda: _emit_agent_message_interaction(**event))
        return msg_id

    def append_messages_batch(
        self,
        session_id: str,
        messages: Any = None,
        compression_lock_holder: str = None,
        chunk_rows: int = None,
        **kwargs: Any,
    ) -> int:
        """hermes v0.20 turn-boundary batch flush (upstream
        ``_SqliteSessionDB.append_messages_batch``). run_agent's DB flush now
        writes the whole turn through this method instead of per-row
        ``append_message`` — a PG impl MUST exist or every cloud-session
        flush raises AttributeError and the transcript is silently lost
        (the exact failure mode test_session_db_contract.py exists for).

        Delegates row-by-row to :meth:`append_message`, which keeps ONE
        encoding path (content sentinel-encoding, timestamp coercion). A single
        transaction commits the turn; a failed row rolls back the whole batch.
        Batch writes do not emit the legacy interactions projection inside the
        transaction; their post-commit callbacks preserve the SPA projection.

        ``compression_lock_holder`` / ``chunk_rows`` are sqlite-side
        concurrency/transaction knobs with no PG equivalent (PG has row-level
        locking and server-side transactions); accepted and ignored.
        """
        from hermes.crm.db import db_session
        from agent.message_metadata import stamp_message_uid
        from hermes_state_messages import SessionMessagesMixin
        inserted = 0
        tool_index = {}
        with db_session() as db, db.transaction():
            for msg in messages or []:
                if not isinstance(msg, dict):
                    continue
                stamp_message_uid(msg)
                SessionMessagesMixin._stamp_tool_call_uids(msg, msg.get("tool_calls"), tool_index)
                row = {k: v for k, v in msg.items() if not k.startswith("_")}
                for live, column in _IDENTITY_ALIASES.items():
                    if live in msg:
                        row[column] = msg[live]
                if row.get("timestamp") is None:
                    row["timestamp"] = time.time()
                row_id = self.append_message(session_id=session_id, _batch_db=db, **row)
                # Stamp only after commit: rolled-back ids must never leak into
                # live dicts and later address another writer's durable row.
                db.after_commit(lambda msg=msg, row_id=row_id, timestamp=row["timestamp"]:
                                msg.update(_row_id=row_id, timestamp=timestamp))
                inserted += 1
            db._maybe_commit(True)
        return inserted

    # ── hermes v0.21.0 additions ────────────────────────────────────────
    # Upstream added these to _SqliteSessionDB and calls them from SHARED
    # code (compression, prompt-cache scoping, cron lifecycle booking). Every
    # call site is try/except-guarded, so a missing method degrades rather
    # than crashing — but the degraded paths are real losses on the cloud
    # runtime, so implement them here instead of registering a gap.

    def get_active_message_watermark(self, session_id: str) -> int:
        """MAX(id) of the session's live rows — the compression watermark.

        Captured at compression START; rows above it at commit time arrived
        concurrently and must survive the compaction verbatim (without a
        trustworthy ceiling the handoff clone can duplicate rows). sqlite
        filters on its ``active`` flag; PG has no such column because
        compaction here REPLACES rows (``replace_messages``) rather than
        flagging them inactive, so every stored row is live by construction
        and a plain MAX(id) is the same watermark.
        """
        if not session_id:
            return 0
        from hermes.crm.db import db_session
        with db_session() as db:
            row = db.fetchone(
                "SELECT COALESCE(MAX(id), 0) AS wm FROM agent_messages "
                "WHERE session_id = %s",
                (session_id,),
            )
        return int((row or {}).get("wm") or 0)

    def session_lifecycle_statuses(
        self, session_ids: List[str]
    ) -> Dict[str, str]:
        """Classify each session from its LAST message row.

        ``{session_id: 'complete'|'interrupted'|'error'|'empty'}`` — mirrors
        upstream's sqlite classifier. Used by the cron scheduler to book a
        run as ``cron_incomplete_no_output`` when the session ended without a
        final assistant message. Same cost shape as upstream: one MAX(id)
        per session plus a join back for that single row.
        """
        ids = [str(s) for s in (session_ids or []) if s]
        if not ids:
            return {}
        from hermes.crm.db import db_session
        with db_session() as db:
            rows = db.fetchall(
                """
                SELECT m.session_id, m.role, m.tool_calls, m.finish_reason
                  FROM agent_messages m
                  JOIN (
                        SELECT session_id, MAX(id) AS max_id
                          FROM agent_messages
                         WHERE session_id = ANY(%s)
                         GROUP BY session_id
                       ) last ON last.session_id = m.session_id
                              AND last.max_id = m.id
                """,
                (ids,),
            ) or []
        out: Dict[str, str] = {sid: "empty" for sid in ids}
        for r in rows:
            sid = r.get("session_id")
            role = (r.get("role") or "").strip()
            finish = (r.get("finish_reason") or "").strip().lower()
            if finish in ("error", "failed"):
                out[sid] = "error"
            elif role == "assistant" and not r.get("tool_calls"):
                out[sid] = "complete"
            else:
                # user turn, assistant with a pending tool call, or a tool
                # result the assistant never answered.
                out[sid] = "interrupted"
        return out

    def get_compression_lineage(self, session_id: str) -> List[str]:
        """Compression ancestors through tip, chronological.

        Drives prompt-cache scoping (a compression continuation must reuse
        its ancestor's cache scope, or every compaction cold-starts the
        provider prefix cache). Mirrors upstream's walk: climb to the
        compression root, then descend through continuation children. A
        child is a CONTINUATION only when its parent ended with
        ``end_reason='compression'``; explicit forks (branch/delegate/tool
        children) terminate the walk — PG marks those with source='tool' or
        the delegate/branch markers in model_config, same as sqlite.
        """
        if not session_id:
            return []
        session = self.get_session(session_id)
        if not session:
            return []
        if self._is_explicit_fork_child_row(session):
            return [session_id]

        from hermes.crm.db import db_session

        def _row(sid):
            return self.get_session(sid) if sid else None

        # Climb to the compression root.
        root = session
        seen_up = {root["id"]}
        while True:
            parent_id = root.get("parent_session_id")
            if not parent_id or self._is_explicit_fork_child_row(root):
                break
            parent = _row(parent_id)
            if not parent or parent["id"] in seen_up:
                break
            if (parent.get("end_reason") or "") != "compression":
                break
            root = parent
            seen_up.add(root["id"])

        lineage = [root["id"]]
        seen = {root["id"]}
        current = root
        while (current.get("end_reason") or "") == "compression":
            with db_session() as db:
                kids = db.fetchall(
                    "SELECT * FROM agent_sessions WHERE parent_session_id = %s "
                    "ORDER BY started_at ASC",
                    (current["id"],),
                ) or []
            next_child = None
            for kid in kids:
                if self._is_explicit_fork_child_row(kid):
                    continue
                next_child = kid
                break
            if not next_child or next_child["id"] in seen:
                break
            lineage.append(next_child["id"])
            seen.add(next_child["id"])
            current = next_child
        return lineage

    @staticmethod
    def _is_explicit_fork_child_row(session: Dict[str, Any]) -> bool:
        """True when the row is a branch / delegate / tool child, not a
        compression continuation (mirrors sqlite's marker check)."""
        if (session or {}).get("source") == "tool":
            return True
        cfg = (session or {}).get("model_config")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = None
        if not isinstance(cfg, dict):
            return False
        parent_id = session.get("parent_session_id")
        for marker in ("_branched_from", "_delegate_from"):
            if parent_id and cfg.get(marker) == parent_id:
                return True
        return False

    def set_turn_meta(self, session_id: str, turn_meta: Any) -> bool:
        """Attach a turn's timing summary to its LAST assistant message.

        ``turn_meta`` (``{total_ms, queued_ms, api_calls, tool_count}``) is only
        known after the whole turn finishes (total wall-clock + queue gap live in
        the gateway), but the assistant rows were already inserted during the
        agent loop. So the gateway calls this at turn end to stamp the final
        assistant row. Best-effort: returns False (logged) on any failure so a
        timing-write hiccup never breaks the reply path.
        """
        if not turn_meta:
            return False
        from hermes.crm.db import db_session
        try:
            with db_session() as db:
                row = db.fetchone(
                    "SELECT id FROM agent_messages "
                    "WHERE session_id = %s AND role = 'assistant' "
                    "ORDER BY timestamp DESC, id DESC LIMIT 1",
                    (session_id,),
                )
                if not row:
                    return False
                db.execute(
                    "UPDATE agent_messages SET turn_meta = %s::jsonb WHERE id = %s",
                    (json.dumps(turn_meta), int(row["id"])),
                )
                # pylint: disable=protected-access
                db._maybe_commit(True)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_turn_meta failed for session %s: %s", session_id, exc)
            return False

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        from hermes.crm.db import db_session
        with db_session() as db:
            rows = db.fetchall(
                "SELECT * FROM agent_messages WHERE session_id = %s "
                "ORDER BY timestamp, id",
                (session_id,),
            ) or []
        return [_normalize_message_row(r) for r in rows]

    def get_messages_around(
        self,
        session_id: str,
        around_message_id: int,
        window: int = 5,
    ) -> Dict[str, Any]:
        """PG mirror of ``_SqliteSessionDB.get_messages_around``.

        Returns ``window`` messages before + the anchor + ``window`` after,
        ordered by id ASC, plus boundary counts so callers can detect
        session edges. Empty window when the anchor isn't in this session.
        """
        if window < 0:
            window = 0
        from hermes.crm.db import db_session
        with db_session() as db:
            anchor_exists = db.fetchone(
                "SELECT 1 FROM agent_messages "
                " WHERE id = %s AND session_id = %s LIMIT 1",
                (around_message_id, session_id),
            )
            if not anchor_exists:
                return {"window": [], "messages_before": 0, "messages_after": 0}
            before_rows = db.fetchall(
                "SELECT * FROM agent_messages "
                " WHERE session_id = %s AND id <= %s "
                " ORDER BY id DESC LIMIT %s",
                (session_id, around_message_id, window + 1),
            ) or []
            after_rows = db.fetchall(
                "SELECT * FROM agent_messages "
                " WHERE session_id = %s AND id > %s "
                " ORDER BY id ASC LIMIT %s",
                (session_id, around_message_id, window),
            ) or []
        rows = list(reversed(before_rows)) + list(after_rows)
        # before_rows includes the anchor; strict-before count is len-1.
        return {
            "window": [_normalize_message_row(r) for r in rows],
            "messages_before": max(0, len(before_rows) - 1),
            "messages_after": len(after_rows),
        }

    def get_anchored_view(
        self,
        session_id: str,
        around_message_id: int,
        window: int = 5,
        bookend: int = 3,
        keep_roles: Optional[Tuple[str, ...]] = ("user", "assistant"),
    ) -> Dict[str, Any]:
        """PG mirror of ``_SqliteSessionDB.get_anchored_view``.

        Anchored window (role-filtered, anchor always kept) plus session
        bookends (first/last ``bookend`` user/assistant messages outside
        the window, non-empty content). Empty bookends when the window
        already touches the session edge.
        """
        if bookend < 0:
            bookend = 0
        primitive = self.get_messages_around(
            session_id, around_message_id, window=window,
        )
        window_rows = primitive["window"]
        if not window_rows:
            return {
                "window": [],
                "messages_before": 0,
                "messages_after": 0,
                "bookend_start": [],
                "bookend_end": [],
            }

        if keep_roles is not None:
            keep_set = set(keep_roles)
            filtered_window = [
                m for m in window_rows
                if m.get("id") == around_message_id or m.get("role") in keep_set
            ]
        else:
            filtered_window = window_rows

        window_min_id = window_rows[0]["id"]
        window_max_id = window_rows[-1]["id"]

        bookend_start_rows: list = []
        bookend_end_rows: list = []
        if bookend > 0:
            from hermes.crm.db import db_session
            role_clause = ""
            role_params: list = []
            if keep_roles is not None:
                role_placeholders = ", ".join(["%s"] * len(keep_roles))
                role_clause = f" AND role IN ({role_placeholders})"
                role_params = list(keep_roles)
            with db_session() as db:
                bookend_start_rows = db.fetchall(
                    f"SELECT * FROM agent_messages "
                    f" WHERE session_id = %s AND id < %s{role_clause} "
                    f"   AND length(coalesce(content, '')) > 0 "
                    f" ORDER BY id ASC LIMIT %s",
                    (session_id, window_min_id, *role_params, bookend),
                ) or []
                bookend_end_rows = db.fetchall(
                    f"SELECT * FROM agent_messages "
                    f" WHERE session_id = %s AND id > %s{role_clause} "
                    f"   AND length(coalesce(content, '')) > 0 "
                    f" ORDER BY id DESC LIMIT %s",
                    (session_id, window_max_id, *role_params, bookend),
                ) or []
                bookend_end_rows = list(reversed(bookend_end_rows))

        return {
            "window": filtered_window,
            "messages_before": primitive["messages_before"],
            "messages_after": primitive["messages_after"],
            "bookend_start": [_normalize_message_row(r) for r in bookend_start_rows],
            "bookend_end": [_normalize_message_row(r) for r in bookend_end_rows],
        }

    def replace_messages(
        self, session_id: str, messages: List[Dict[str, Any]],
        reject_active_turn_lease: bool = False,
        **kwargs: Any,
    ) -> None:
        """Atomically replace every message for a session.

        ``reject_active_turn_lease`` (hermes v0.21) IS passed on a live cloud
        path — ``/retry`` on an IM channel goes slash_commands →
        rewrite_transcript → here — so the param must exist or the retry dies
        with "transcript was not changed". Accepted and ignored: it asks the
        store to refuse the rewrite while another turn holds a message-row
        turn lease, and leases are a sqlite-only column (v0.21's
        ``turn_lease_holder``) that PG never writes, so there is never a lease
        to reject and the rewrite proceeds exactly as it did pre-v0.21.

        Mirrors :meth:`hermes_state.SessionDB.replace_messages`. Used by
        transcript-rewrite flows (/retry, /undo, /compress). The delete +
        reinsert pair commits as a single transaction so a mid-rewrite
        crash can't leave PG with a partial transcript.

        Multimodal/codex content is sentinel-encoded the same way
        :meth:`append_message` does — so a list-of-parts ``content`` that
        round-tripped through ``get_messages_as_conversation`` survives
        a /retry rewrite intact.
        """
        from hermes.crm.db import db_session
        with db_session() as db, db.transaction():
            db.execute(
                "DELETE FROM agent_messages WHERE session_id = %s",
                (session_id,),
            )
            # Recompute the session counters since the trigger doesn't
            # cover the bulk-replace path.
            num_msgs = len(messages)
            num_tool_calls = sum(
                (
                    len(m.get("tool_calls"))
                    if isinstance(m.get("tool_calls"), list)
                    else (1 if m.get("tool_calls") else 0)
                )
                for m in messages
            )
            db.execute(
                "UPDATE agent_sessions SET "
                "  message_count = %s, tool_call_count = %s "
                "WHERE id = %s",
                (num_msgs, num_tool_calls, session_id),
            )
            # ``now`` is the fallback for messages that don't carry their
            # own timestamp (e.g. freshly-rewritten /retry turns). Replays
            # of historical messages MUST preserve original timestamps so
            # ordering, last_active, and search-result context don't get
            # collapsed onto a single instant.
            now = time.time()
            tool_index = {}
            for m in messages:
                tc = m.get("tool_calls")
                tc_json = json.dumps(tc) if tc else None
                rd = m.get("reasoning_details")
                rd_json = json.dumps(rd) if rd else None
                cri = m.get("codex_reasoning_items")
                cri_json = json.dumps(cri) if cri else None
                cmi = m.get("codex_message_items")
                cmi_json = json.dumps(cmi) if cmi else None
                content = _encode_content(m.get("content"))
                # v0.20 display fields round-trip through rewrite flows the
                # same way sqlite's _insert_message_rows persists them — a
                # /retry or compaction rewrite must not strip the 'hidden'
                # flag off a summary-carrier row (#80622) nor drop the
                # api_content replay sidecar.
                api_content = m.get("api_content")
                display_kind = m.get("display_kind")
                db.execute(
                    """
                    INSERT INTO agent_messages
                      (session_id, role, content, tool_call_id, tool_calls,
                       tool_name, timestamp, token_count, finish_reason,
                       reasoning, reasoning_content, reasoning_details,
                       codex_reasoning_items, codex_message_items,
                       api_content, display_kind, display_metadata,
                       duration_ms, turn_meta, platform_message_id, observed,
                       message_uid, absorbed_message_uids, tool_call_uids,
                       tool_call_uid, effect_disposition)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                            %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                            %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s,
                            %s, %s::jsonb, %s::jsonb, %s, %s)
                    """,
                    (
                        session_id, m.get("role"), content,
                        m.get("tool_call_id"), tc_json, m.get("tool_name"),
                        m.get("timestamp", now),
                        m.get("token_count"), m.get("finish_reason"),
                        m.get("reasoning"), m.get("reasoning_content"),
                        rd_json, cri_json, cmi_json,
                        api_content if isinstance(api_content, str) else None,
                        display_kind if isinstance(display_kind, str) else None,
                        _encode_display_metadata(m.get("display_metadata")),
                        m.get("duration_ms"), json.dumps(m["turn_meta"]) if m.get("turn_meta") is not None else None,
                        m.get("platform_message_id"), int(bool(m.get("observed"))),
                        *_message_identity(m, tool_index), m.get("effect_disposition"),
                    ),
                )
            # pylint: disable=protected-access
            db._maybe_commit(True)

    def get_messages_as_conversation(
        self, session_id: str,
        include_ancestors: bool = False,
        repair_alternation: bool = False,
        include_row_ids: bool = False,
    ) -> List[Dict[str, Any]]:
        """…

        ``repair_alternation`` (v0.20): NOT TUI-only as first assumed —
        gateway/session.py transcript reads now pass it on the CLOUD path,
        and before this param existed every IM-channel transcript read
        raised TypeError and fell back to an EMPTY conversation (observed
        live: "Transcript read failed … unexpected keyword argument
        'repair_alternation'" on every feishu DM turn — the bot lost its
        history each turn). Accepted and ignored: PG returns rows as
        stored, which is exactly the v0.19 behavior the gateway ran on.
        """
        from hermes.crm.db import db_session
        with db_session() as db:
            rows = db.fetchall(
                """
                SELECT id, role, content, tool_call_id, tool_calls, tool_name,
                       reasoning, reasoning_content, reasoning_details,
                       codex_reasoning_items, codex_message_items,
                       finish_reason, api_content, display_kind,
                       display_metadata, duration_ms, turn_meta, timestamp,
                       platform_message_id, observed, message_uid,
                       absorbed_message_uids, tool_call_uids, tool_call_uid,
                       effect_disposition
                  FROM agent_messages
                 WHERE session_id = %s
                 ORDER BY timestamp, id
                """,
                (session_id,),
            ) or []
        result = []
        for r in rows:
            # Decode the sentinel-prefixed JSON so multimodal content
            # round-trips as list/dict (matches sqlite SessionDB).
            msg = {"role": r["role"], "content": _decode_content(r.get("content"))}
            # Mirror sqlite _rows_to_conversation: api_content is returned
            # VERBATIM (no sanitize/strip — the replay path substitutes it
            # for content to keep the provider prompt-cache prefix
            # byte-stable); display_kind/display_metadata ride along so the
            # gateway's replay filters ('hidden' ghost rows, #80622/#81841)
            # and display demotion see them, and its pre-send strip removes
            # them from every outgoing provider payload.
            if r.get("api_content"):
                msg["api_content"] = r["api_content"]
            if r.get("display_kind"):
                msg["display_kind"] = r["display_kind"]
            if r.get("display_metadata"):
                decoded_meta = _decode_display_metadata(r["display_metadata"])
                if decoded_meta is not None:
                    msg["display_metadata"] = decoded_meta
            if r.get("tool_call_id"):
                msg["tool_call_id"] = r["tool_call_id"]
            if r.get("tool_name"):
                msg["tool_name"] = r["tool_name"]
            if r.get("tool_calls") is not None:
                # JSONB returns parsed already.
                msg["tool_calls"] = r["tool_calls"]
            # finish_reason must round-trip too — providers like DeepSeek
            # use it to decide whether to feed reasoning_content back in.
            if r.get("finish_reason"):
                msg["finish_reason"] = r["finish_reason"]
            if r["role"] == "assistant":
                if r.get("reasoning"):
                    msg["reasoning"] = r["reasoning"]
                # ``reasoning_content`` of ""  must still surface — Kimi
                # replay relies on the empty-string sentinel to clear
                # any prior reasoning context. Use ``is not None`` not
                # truthiness.
                if r.get("reasoning_content") is not None:
                    msg["reasoning_content"] = r["reasoning_content"]
                if r.get("reasoning_details") is not None:
                    msg["reasoning_details"] = r["reasoning_details"]
                if r.get("codex_reasoning_items") is not None:
                    msg["codex_reasoning_items"] = r["codex_reasoning_items"]
                if r.get("codex_message_items") is not None:
                    msg["codex_message_items"] = r["codex_message_items"]
            from hermes_state_identity import _restore_identity_columns
            _restore_identity_columns(r, msg)
            if include_row_ids:
                msg["_row_id"] = int(r["id"])
            for field in ("duration_ms", "turn_meta", "timestamp", "platform_message_id", "observed", "effect_disposition"):
                if r.get(field) is not None:
                    msg[field] = r[field]
            result.append(msg)
        return result

    # =========================================================================
    # Search
    # =========================================================================

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        # FTS5-specific sanitizer; PG path uses websearch_to_tsquery so
        # we don't need it. Keep as static for API parity.
        from hermes_state import _SqliteSessionDB
        return _SqliteSessionDB._sanitize_fts5_query(query)

    def search_messages(
        self,
        query: str,
        source_filter: List[str] = None,
        exclude_sources: List[str] = None,
        role_filter: List[str] = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = None,
        fields: List[str] = None,
        after_ts: Optional[int] = None,
        before_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Search past messages — Charter §一原语 ② Memory Substrate.

        Strategy:
          - Short keyword-y queries (UUID fragments, single words, code
            tokens) → ``tsvector`` lexical match. Better for "find this
            error message verbatim" / "find my conversation about
            session_id X" cases.
          - Natural-language queries → ``pgvector`` cosine similarity on
            ``agent_messages.embedding`` (alembic 0032). Better for
            "find sessions where I discussed pricing strategy" — the
            real use case behind ``tools/session_search_tool.py`` which
            historically asked Gemini Flash to re-rank FTS5 hits, i.e.
            already aiming for semantic relevance.

        Filter clauses (role / source / exclude_sources) apply to both
        modalities via the same SQL WHERE.

        ``fields`` (hermes v0.20, sqlite side) narrows the RETURNED row
        shape for cheap discovery scans. It IS passed on a live cloud path:
        the default ``hermes-api-server`` composite includes
        ``session_search``, whose discovery mode calls
        ``search_messages(..., fields=...)`` — omitting the param here
        TypeError'd every discovery-mode search on PG. Accepted and
        ignored: PG returns its full row shape (a superset of every
        requested projection), which callers index by key, so extra
        fields are harmless.
        """
        from hermes.crm.db import db_session
        if not query or not query.strip():
            return []
        q = query.strip()
        is_keyword = _looks_like_keyword_lookup(q)

        # Build the filter WHERE that's shared by both modalities.
        clauses: list = []
        args: list = []
        if source_filter:
            placeholders = ", ".join(["%s"] * len(source_filter))
            clauses.append(f"s.source IN ({placeholders})")
            args.extend(source_filter)
        if exclude_sources:
            placeholders = ", ".join(["%s"] * len(exclude_sources))
            clauses.append(f"s.source NOT IN ({placeholders})")
            args.extend(exclude_sources)
        if after_ts is not None:
            clauses.append("s.started_at >= %s")
            args.append(int(after_ts))
        if before_ts is not None:
            clauses.append("s.started_at < %s")
            args.append(int(before_ts))
        if role_filter:
            placeholders = ", ".join(["%s"] * len(role_filter))
            clauses.append(f"m.role IN ({placeholders})")
            args.extend(role_filter)

        # Normalise sort (mirrors upstream _SqliteSessionDB.search_messages):
        # only "newest" / "oldest" honoured; anything else falls back to the
        # path-specific default ordering.
        sort_norm: Optional[str] = None
        if isinstance(sort, str):
            candidate = sort.strip().lower()
            if candidate in ("newest", "oldest"):
                sort_norm = candidate

        if is_keyword:
            return self._search_by_tsvector(
                q, clauses, args, limit=limit, offset=offset, sort=sort_norm,
            )
        # Semantic path. Fall through to keyword if embedding fails
        # (e.g. embedder offline) — degrades gracefully.
        try:
            return self._search_by_embedding(
                q, clauses, args, limit=limit, offset=offset, sort=sort_norm,
            )
        except Exception:
            logger.exception("search_messages_pgvector_failed; falling back to tsvector")
            return self._search_by_tsvector(
                q, clauses, args, limit=limit, offset=offset, sort=sort_norm,
            )

    def _search_by_tsvector(
        self, q: str, extra_clauses: list, extra_args: list,
        *, limit: int, offset: int, sort: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        from hermes.crm.db import db_session
        all_clauses = [
            "to_tsvector('simple', coalesce(m.content, '')) @@ "
            "websearch_to_tsquery('simple', %s)"
        ] + extra_clauses
        # Use ts_headline for snippet generation (replaces sqlite FTS5
        # snippet()). Fall back to raw content[:200] if ts_headline fails
        # (e.g. malformed query).
        all_args: list = [q] + list(extra_args) + [int(limit), int(offset)]
        where = " AND ".join(all_clauses)
        # Default = timestamp DESC (existing cloud behaviour). sort="oldest"
        # flips direction. "newest" matches the default.
        order_by_sql = "ORDER BY m.timestamp ASC" if sort == "oldest" else "ORDER BY m.timestamp DESC"
        with db_session() as db:
            rows = db.fetchall(
                f"""
                SELECT
                    m.id, m.session_id, m.role, m.content,
                    m.tool_calls, m.tool_call_id, m.tool_name,
                    m.timestamp, m.finish_reason,
                    s.source, s.model, s.title,
                    s.started_at AS session_started
                  FROM agent_messages m
                  JOIN agent_sessions s ON s.id = m.session_id
                 WHERE {where}
                 {order_by_sql}
                 LIMIT %s OFFSET %s
                """,
                tuple(all_args),
            ) or []
        return self._enrich_with_snippet_and_context(rows)

    def _search_by_embedding(
        self, q: str, extra_clauses: list, extra_args: list,
        *, limit: int, offset: int, sort: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        from hermes.crm.db import db_session
        from hermes.memory.embedder import embed
        qvec = embed(q)
        if not qvec:
            return []
        # Only consider rows that already have an embedding — the 5-min
        # ingest_batch fills them. Brand-new messages aren't searchable
        # for the first window, which is fine for this use case (the
        # tool surfaces past sessions).
        all_clauses = ["m.embedding IS NOT NULL"] + extra_clauses
        all_args: list = list(extra_args)
        where = " AND ".join(all_clauses)
        # Default = cosine distance (most-similar first). With sort set,
        # timestamp becomes primary key and similarity is the tiebreaker —
        # mirrors upstream "timestamp DESC, rank" pairing.
        if sort == "newest":
            order_by_sql = "ORDER BY m.timestamp DESC, m.embedding <=> %s::vector"
        elif sort == "oldest":
            order_by_sql = "ORDER BY m.timestamp ASC, m.embedding <=> %s::vector"
        else:
            order_by_sql = "ORDER BY m.embedding <=> %s::vector"
        with db_session() as db:
            rows = db.fetchall(
                f"""
                SELECT
                    m.id, m.session_id, m.role, m.content,
                    m.tool_calls, m.tool_call_id, m.tool_name,
                    m.timestamp, m.finish_reason,
                    s.source, s.model, s.title,
                    s.started_at AS session_started,
                    1 - (m.embedding <=> %s::vector) AS similarity
                  FROM agent_messages m
                  JOIN agent_sessions s ON s.id = m.session_id
                 WHERE {where}
                 {order_by_sql}
                 LIMIT %s OFFSET %s
                """,
                (qvec, *all_args, qvec, int(limit), int(offset)),
            ) or []
        return self._enrich_with_snippet_and_context(rows)

    def _enrich_with_snippet_and_context(
        self, rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Add ``snippet`` (200-char preview) and ``context`` (the
        prev + match + next messages in the same session, in time order)
        to each result row. Mirrors upstream FTS5 search_messages shape
        so downstream consumers (``tools/session_search_tool.py``) work
        unchanged.
        """
        if not rows:
            return []
        from hermes.crm.db import db_session
        out: List[Dict[str, Any]] = []
        with db_session() as db:
            for r in rows:
                d = _normalize_message_row(r)
                content = d.get("content") or ""
                d["snippet"] = (
                    content[:200] + ("…" if len(content) > 200 else "")
                )
                sid = d.get("session_id")
                ts = d.get("timestamp")
                mid = d.get("id")
                # Previous message (same session, earlier in time).
                prev = db.fetchone(
                    "SELECT role, content FROM agent_messages "
                    " WHERE session_id = %s AND (timestamp, id) < (%s, %s) "
                    " ORDER BY timestamp DESC, id DESC LIMIT 1",
                    (sid, ts, mid),
                )
                # Next message (same session, later in time).
                nxt = db.fetchone(
                    "SELECT role, content FROM agent_messages "
                    " WHERE session_id = %s AND (timestamp, id) > (%s, %s) "
                    " ORDER BY timestamp ASC, id ASC LIMIT 1",
                    (sid, ts, mid),
                )
                # Decode the sentinel-prefixed JSON (multimodal/codex turns)
                # so search-result context renders as list/dict, not as the
                # opaque encoded blob. ``d['content']`` was already decoded
                # by ``_normalize_message_row`` upstream of this call.
                ctx: list = []
                if prev:
                    ctx.append({"role": prev.get("role"),
                                "content": _decode_content(prev.get("content"))})
                ctx.append({"role": d.get("role"),
                            "content": d.get("content")})
                if nxt:
                    ctx.append({"role": nxt.get("role"),
                                "content": _decode_content(nxt.get("content"))})
                d["context"] = ctx
                out.append(d)
        return out

    # =========================================================================
    # Counts / export / prune (minimal stubs for tests)
    # =========================================================================

    def session_count(self, source: str = None) -> int:
        from hermes.crm.db import db_session
        with db_session() as db:
            if source:
                return int(db.fetchcolumn(
                    "SELECT COUNT(*) FROM agent_sessions WHERE source = %s",
                    (source,),
                ) or 0)
            return int(db.fetchcolumn(
                "SELECT COUNT(*) FROM agent_sessions"
            ) or 0)

    def message_count(self, session_id: str = None) -> int:
        from hermes.crm.db import db_session
        with db_session() as db:
            if session_id:
                return int(db.fetchcolumn(
                    "SELECT COUNT(*) FROM agent_messages WHERE session_id = %s",
                    (session_id,),
                ) or 0)
            return int(db.fetchcolumn(
                "SELECT COUNT(*) FROM agent_messages"
            ) or 0)

    def delete_session(self, session_id: str) -> bool:
        from hermes.crm.db import db_session
        # Charter §一: agent_sessions FK has ON DELETE SET NULL for
        # parent_session_id, so children become root sessions rather
        # than vanishing. Mirrors upstream's "orphan children" behaviour
        # (PR #6513). agent_messages CASCADE.
        with db_session() as db:
            db.execute(
                "UPDATE agent_sessions SET parent_session_id = NULL "
                "WHERE parent_session_id = %s",
                (session_id,),
            )
            row = db.fetchone(
                "DELETE FROM agent_sessions WHERE id = %s RETURNING id",
                (session_id,),
            )
        return row is not None

    def clear_messages(self, session_id: str) -> None:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.execute(
                "DELETE FROM agent_messages WHERE session_id = %s",
                (session_id,),
            )
            db.execute(
                "UPDATE agent_sessions SET "
                "  message_count = 0, tool_call_count = 0 "
                "WHERE id = %s",
                (session_id,),
            )

    def export_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        # Upstream returns {**session_fields, "messages": [...]} — fields
        # at top level, not nested under a "session" key.
        s = self.get_session(session_id)
        if not s:
            return None
        return {**s, "messages": self.get_messages(session_id)}

    def export_all(self, source: str = None) -> List[Dict[str, Any]]:
        return [self.export_session(s["id"])
                for s in self.list_sessions_rich(limit=10_000, source=source)]

    def prune_sessions(
        self, older_than_days: int = 90, source: str = None,
    ) -> int:
        """Delete sessions older than N days. Mirrors upstream:
        only prunes ENDED sessions (ended_at IS NOT NULL); child sessions
        outside the prune window are orphaned (parent_session_id NULL)
        rather than cascade-deleted.
        """
        from hermes.crm.db import db_session
        cutoff = time.time() - (max(1, older_than_days) * 86400)
        clauses = ["started_at < %s", "ended_at IS NOT NULL"]
        args: list = [cutoff]
        if source:
            clauses.append("source = %s")
            args.append(source)
        where = " AND ".join(clauses)
        with db_session() as db:
            # Re-parent children before delete (mirror upstream behaviour).
            db.execute(
                f"UPDATE agent_sessions SET parent_session_id = NULL "
                f"WHERE parent_session_id IN ("
                f"  SELECT id FROM agent_sessions WHERE {where})",
                tuple(args),
            )
            rows = db.fetchall(
                f"DELETE FROM agent_sessions WHERE {where} "
                f"RETURNING id",
                tuple(args),
            ) or []
        return len(rows)

    def search_sessions(
        self,
        source: str = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by source. Mirrors the
        upstream sqlite signature (sorted by started_at DESC).
        """
        from hermes.crm.db import db_session
        with db_session() as db:
            if source:
                rows = db.fetchall(
                    "SELECT * FROM agent_sessions "
                    "WHERE source = %s "
                    "ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    (source, int(limit), int(offset)),
                ) or []
            else:
                rows = db.fetchall(
                    "SELECT * FROM agent_sessions "
                    "ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    (int(limit), int(offset)),
                ) or []
        return [dict(r) for r in rows]


# =========================================================================
# Helpers
# =========================================================================


_KEYWORD_RE = __import__("re").compile(
    # A "keyword lookup" is short (≤ 3 tokens) AND every token is either
    # a hex/uuid fragment, a code identifier (snake_case / camelCase), an
    # error code, or a sufficiently rare-looking word. Natural-language
    # queries fail this — they have whitespace + multiple tokens + use
    # natural words.
    r"^[\w\-/.:]+$"
)


def _looks_like_keyword_lookup(q: str) -> bool:
    """Heuristic: is this a "find that exact thing" query?

    Cheaper to be wrong (degrades to tsvector for natural language → still
    works, just less semantic) than to deny a verbatim search of a UUID
    or error code by routing it through embeddings (which would never
    match the exact token).
    """
    s = q.strip()
    if not s:
        return False
    # Quoted phrase ("...") = explicit lexical intent — go straight to
    # tsvector so websearch_to_tsquery preserves phrase semantics.
    if s.startswith('"') or s.startswith("'"):
        return True
    tokens = s.split()
    if len(tokens) <= 1:
        return True
    if len(tokens) > 3:
        return False
    # Every token must look code-y / identifier-y / hex-y.
    return all(_KEYWORD_RE.match(t) for t in tokens)


def _normalize_session_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out = dict(row)
    # model_config: jsonb → already parsed by psycopg, but upstream tests
    # often expect a JSON string when stored that way. We expose dict.
    return out


def _encode_display_metadata(display_metadata: Any) -> Optional[str]:
    """Serialize ``display_metadata`` for its JSONB column (PG mirror of
    ``_SqliteSessionDB._encode_display_metadata``).

    Import/replace paths can hand us an already-serialized JSON string;
    ``json.dumps`` on that would store a quoted JSON string and readers
    would get a ``str`` instead of a dict. Only dict-shaped metadata is
    accepted — anything else is dropped with a warning, matching sqlite.
    """
    if not display_metadata:
        return None
    if isinstance(display_metadata, str):
        try:
            parsed = json.loads(display_metadata)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Ignoring non-JSON display metadata on write")
            return None
        if not isinstance(parsed, dict):
            logger.warning("Ignoring non-object display metadata on write")
            return None
        return json.dumps(parsed)
    if isinstance(display_metadata, dict):
        return json.dumps(display_metadata)
    logger.warning(
        "Ignoring unexpected display metadata type on write: %s",
        type(display_metadata).__name__,
    )
    return None


def _decode_display_metadata(raw: Any) -> Optional[Dict[str, Any]]:
    """Decode a ``display_metadata`` value into the dict every reader expects
    (PG mirror of ``_SqliteSessionDB._decode_display_metadata``).

    JSONB normally comes back parsed already; a str means a double-encoded
    legacy row — unwrap it. Non-dict results are dropped with a warning so
    consumers can rely on ``dict | None``.
    """
    if raw is None:
        return None
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(meta, str):
            meta = json.loads(meta)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Ignoring invalid display metadata on message row")
        return None
    if not isinstance(meta, dict):
        logger.warning("Ignoring non-object display metadata on message row")
        return None
    return meta


def _normalize_message_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    # tool_calls: JSONB returns parsed list already; sqlite path
    # returned a string and the upstream caller json.loads'd it. Match
    # the post-parse form so callers don't need to branch.
    if "content" in out:
        out["content"] = _decode_content(out.get("content"))
    # ``observed`` is stored 0/1; expose it as a real bool only when truthy,
    # mirroring upstream SessionDB.get_messages (which sets msg["observed"]=True
    # for spectator rows). The gateway's replay filter checks ``msg.get("observed")``
    # to withhold these from replayable history.
    if out.get("observed"):
        out["observed"] = True
    # Mirror sqlite get_messages: display_metadata always reaches callers as
    # dict | None, never a raw JSON string (double-encoded legacy rows).
    if out.get("display_metadata") is not None:
        out["display_metadata"] = _decode_display_metadata(out["display_metadata"])
    from hermes_state_identity import _restore_identity_columns
    _restore_identity_columns({key: out.get(key) for key in
        ("message_uid", "absorbed_message_uids", "tool_call_uids", "tool_call_uid")}, out)
    return out


# ── Multimodal/codex content encoding (mirror of hermes_state.SessionDB) ──
# sqlite uses ``\x00json:`` as the sentinel because sqlite3 can't bind
# list/dict params directly. We can't reuse that exact sentinel here:
# PostgreSQL TEXT columns reject NUL bytes. Use a NUL-free zero-width-
# space + tag instead — unprintable in normal content, won't collide in
# practice, and PG accepts it. Decode tolerates both sentinels so any
# legacy rows written under the sqlite sentinel still round-trip.

_CONTENT_JSON_PREFIX = "​hermes-json:"
_LEGACY_SQLITE_PREFIX = "\x00json:"


class _PgContentCodec:
    _CONTENT_JSON_PREFIX = _CONTENT_JSON_PREFIX


def _encode_content(content: Any) -> Any:
    """Upstream escaping/surrogate rules with PG's NUL-free storage prefix."""
    from hermes_state_messages import SessionMessagesMixin
    from agent.message_sanitization import _sanitize_surrogates
    if isinstance(content, str) and content.startswith(_LEGACY_SQLITE_PREFIX):
        # The decoder accepts legacy SQLite rows too: a literal legacy marker
        # must be wrapped, and its NUL cannot be bound to a PostgreSQL TEXT.
        return _CONTENT_JSON_PREFIX + json.dumps(_sanitize_surrogates(content))
    return SessionMessagesMixin._encode_content.__func__(_PgContentCodec, content)


def _decode_content(content: Any) -> Any:
    """Reverse :func:`_encode_content`. Tolerates the legacy sqlite-sentinel
    so a sqlite-mode write that survived into PG still decodes correctly."""
    if isinstance(content, str):
        for prefix in (_CONTENT_JSON_PREFIX, _LEGACY_SQLITE_PREFIX):
            if content.startswith(prefix):
                try:
                    return json.loads(content[len(prefix):])
                except (json.JSONDecodeError, TypeError):
                    return content
    return content


__all__ = ["PgSessionDB"]


_IDENTITY_ALIASES = {"_absorbed_message_uids": "absorbed_message_uids", "_tool_call_uids": "tool_call_uids", "_tool_call_uid": "tool_call_uid"}


def _message_identity(msg, tool_index=None):
    # Reuse the upstream codec: import/export column names and live keys have
    # identical normalization, including repeated provider ids' occurrence lists.
    from agent.message_metadata import stamp_message_uid
    from hermes_state_identity import _absorbed_uids_json, _tool_call_uids_json, _tool_call_uid_or_none
    from hermes_state_messages import SessionMessagesMixin
    for live, column in _IDENTITY_ALIASES.items():
        if live not in msg and column in msg:
            msg[live] = msg[column]
    stamp_message_uid(msg)
    SessionMessagesMixin._stamp_tool_call_uids(msg, msg.get("tool_calls"), tool_index if tool_index is not None else {})
    return (msg["message_uid"], _absorbed_uids_json(msg), _tool_call_uids_json(msg), _tool_call_uid_or_none(msg))
