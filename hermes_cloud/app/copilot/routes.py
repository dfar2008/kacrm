"""``POST /api/copilot/crm/send`` — single round-trip Copilot entrypoint.

**Phase 4 Stage D (plan v2.2 §4.2)** — the cloud-side LLM loop is
gone. This route is now a thin transport: it builds a
``HermesRequestEnvelope`` from the SPA payload, dispatches via
``proxy.dispatch_to_hermes``, persists the user + assistant turns as
``interactions`` rows, and returns the rendered reply.

What disappeared in Stage D:

* ``copilot/tools.py`` — every tool dispatch (update_record /
  create_record / add_note / list_related_records / propose_*) now
  lives upstream in hermes (patches 24-28). The cloud no longer
  parses tool_calls or runs ``execute_tool``.
* ``chat_with_tools(...)`` agent loop in this file — replaced by a
  single ``dispatch_to_hermes`` call. Cap of 3 rounds, system prompt
  builder, field catalog, thinking-mode reflective-field round-trip,
  and the no-text-reply executions summariser are all removed.
* ``_phase4_shadow_dispatch`` — Stage A double-write only existed to
  measure the new path against the legacy one. With the legacy path
  retired, the shadow is the canonical path; nothing to compare.

What stayed:

* ``GET /api/copilot/crm/history`` — pure SQL re-hydration of the
  conversation timeline; no LLM, no tools.
* The user-turn + assistant-turn ``interactions`` writes, so the SPA
  Copilot panel keeps showing history exactly as before.

Request / response shape unchanged so the SPA frontend doesn't need
a coordinated release.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from hermes_cloud.app.bridge.identity import current_user as bridge_current_user
from hermes.crm.db import DbWrapper, db_session, get_db
from hermes.crm.models.modules_db import ModulesDb
from hermes.crm.models.users_db import UsersDb
from hermes_cloud.app.interactions.emitter import emit
from hermes_cloud.app.proxy.dispatch import dispatch_to_hermes
from hermes_cloud.app.surface import SurfaceKind, inject_context

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/copilot", tags=["copilot"])


def _require_user(request: Request) -> Dict[str, Any]:
    """Same semantics as crm.routes._require_user — duplicated to avoid
    a cyclic import. 401 if session cookie invalid or user inactive.

    Charter v2 §一系统 — opens its own short ``db_session`` instead of
    ``Depends(get_db)``. FastAPI's request-scoped db dependency stays
    open until the response body is fully sent, which for
    ``/crm/send`` means across the multi-minute ``dispatch_to_hermes``
    call — pinning a PG connection in "idle in transaction" and
    eventually exhausting the pool. This auth lookup is microseconds;
    a fresh short session has zero overhead.
    """
    account = bridge_current_user(request)
    try:
        uid = int(account.id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="numeric user id required")
    with db_session() as db:
        row = UsersDb(db).get_user_by_id(uid)
    if row is None or row.get("deleted") or row.get("status") != 1:
        raise HTTPException(status_code=401, detail="user inactive")
    return row


def _bearer(app: Any) -> str:
    """Pull the cloud's internal-key for hermes auth. Empty string is
    fine — dispatch_to_hermes treats it as "no Authorization header"
    which the test-time stubs accept."""
    try:
        return str(app.state.config.internal_key or "")
    except Exception:
        return ""


def _copilot_profile_id(request: Request, user_id: str) -> str:
    """Match the model picker's owned-profile resolution for credentials and execution."""
    store = getattr(request.app.state, "profile_store", None)
    requested = request.headers.get("X-Profile-Id", "").strip()
    if requested:
        profile = store.get_profile(requested) if store is not None else None
        if profile is None or profile.user_id != user_id or not profile.is_active:
            raise HTTPException(status_code=403, detail={"error": {
                "message": "Invalid or inaccessible profile.",
                "type": "forbidden", "code": "invalid_profile",
            }})
        return requested
    # Minimal standalone route consumers may have no profile store.
    if store is None:
        return user_id
    return store.ensure_default_profile(user_id=user_id).id


def register_routes(app) -> None:
    @router.post("/crm/send")
    async def copilot_crm_send(
        payload: Dict[str, Any],
        request: Request,
        user: Dict[str, Any] = Depends(_require_user),
    ) -> Dict[str, Any]:
        # Charter v2 §一系统 — NO ``db: DbWrapper = Depends(get_db)``.
        # FastAPI's request-scoped db cleanup runs only after the
        # response body is fully sent. ``dispatch_to_hermes`` below can
        # take 4+ minutes; holding db across that pins a PG connection
        # in "idle in transaction" and after ~20 turns the pool is
        # exhausted → cloud hangs. We open short ``db_session()``
        # blocks around each DB use instead. See 2026-05-15 audit.
        module_name = str(payload.get("module") or "").strip()
        # record_id is optional — list-mode (no record selected) uses
        # SPA_CHATBOX surface; record-mode uses SPA_RECORD_COPILOT so
        # patch 27's ACL applies the right tool restriction.
        rid_raw = payload.get("record_id")
        record_id: Optional[int] = None
        if rid_raw not in (None, "", "null"):
            try:
                record_id = int(rid_raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="record_id must be int when provided",
                )
        message = str(payload.get("message") or "").strip()
        # Optional attachments — same normalized shape the chat path uses
        # (``{kind:"image"|"document", filename, data_url?|text?}``). The
        # SPA uploads via /api/chat/attachments first, then echoes the
        # returned list here. An attachment-only turn (no typed text) is
        # allowed: the files themselves are the message.
        raw_atts = payload.get("attachments")
        attachments: List[Dict[str, Any]] = (
            [a for a in raw_atts if isinstance(a, dict)]
            if isinstance(raw_atts, list) else []
        )
        if not module_name or (not message and not attachments):
            raise HTTPException(status_code=400, detail="module and message required")

        # Optional chat-box ModelPicker payload. If absent, the user's
        # profile default model is used by the downstream hermes proxy.
        # If invalid for this user's active list, we surface a 400 here
        # rather than letting the agent fall back silently.
        profile_id = _copilot_profile_id(request, str(user["id"]))
        model_override = payload.get("model")
        from hermes_cloud.app.llm_creds import (
            resolve_for_user, NoProfileModelConfigured, ModelNotInActiveList,
        )
        try:
            creds = resolve_for_user(
                request.app.state.config_store,
                str(user["id"]),
                model_override=model_override,
                profile_id=profile_id,
            )
        except (NoProfileModelConfigured, ModelNotInActiveList) as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }},
            )

        # ── Phase 1: short db_session — module lookup + user msg ──────
        with db_session() as db:
            mod = ModulesDb(db).get_module_by_name(module_name)
            if not mod:
                raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found")

            # Persist the user turn first (pre-dispatch). The user's
            # question survives even if hermes is down. Charter §定律 1.
            actors = [{"type": "user", "id": int(user["id"])}]
            if record_id is not None:
                subjects = [{"module": module_name, "id": int(record_id)}]
            else:
                subjects = [{"module": module_name}]
            # Stamp attachment filenames into the persisted text so the
            # re-hydrated history shows that files were sent (the inlined
            # image data_url / doc text is too large to store verbatim).
            persisted_text = message
            if attachments:
                names = "、".join(
                    str(a.get("filename") or "file") for a in attachments
                )
                marker = f"📎 {names}"
                persisted_text = f"{message}\n{marker}" if message else marker
            try:
                emit(
                    kind="chat",
                    subjects=subjects,
                    actors=actors,
                    content={"text": persisted_text, "role": "user"},
                    source="copilot:user",
                    db=db,
                )
            except Exception:
                logger.exception("copilot_persist_user_msg_failed")
        # ← db released; long dispatch call below holds NO pg connection.

        # ── Phase 2: NO db held — long LLM dispatch (can take 4+ min) ─
        surface = (
            SurfaceKind.SPA_RECORD_COPILOT
            if record_id is not None
            else SurfaceKind.SPA_CHATBOX
        )
        envelope = inject_context(
            text=message,
            user_id=int(user["id"]),
            surface=surface,
            record_ref=(module_name, record_id) if record_id is not None else None,
            attachments=attachments,
            # Bind the caller's data scope so the agent's crm_search is RBAC-
            # limited to exactly what this user may see (= the SPA's
            # _build_owner_scope). 'team' resolves via viewable_user_ids: an IC
            # collapses to self, a manager gets self+managed-departments, an
            # admin is unrestricted on the hermes side. Without this the agent
            # searched every tenant's records (cross-department read leak).
            scope="team",
        )
        try:
            result = await dispatch_to_hermes(
                app=request.app, envelope=envelope, profile_id=profile_id,
                bearer_key=_bearer(request.app),
                body_extras={"model": creds.model},
                # Multi-step CRM writes regularly exceed the generic 60s budget.
                # Stay below the production reverse proxy's 300s timeout.
                timeout_sec=240.0,
            )
        except Exception as exc:
            logger.exception("copilot_dispatch_failed: %s", exc)
            return {
                "reply": "",
                "tool_executions": [],
                "fallback": True,
                "error": str(exc),
            }

        reply_text = result.reply_text or ""
        tool_executions = result.tool_executions or []
        if result.status_code >= 400:
            logger.warning(
                "copilot_dispatch status=%s body_head=%r",
                result.status_code, (result.raw_body or b"")[:200],
            )
        if not reply_text and result.status_code >= 400:
            return {
                "reply": "",
                "tool_executions": tool_executions,
                "fallback": True,
                "error": f"hermes status {result.status_code}",
            }
        if not reply_text:
            reply_text = "（没有产生回复）"

        # ── Phase 3: short db_session — persist assistant turn ────────
        try:
            with db_session() as db:
                emit(
                    kind="copilot_reply",
                    subjects=subjects,
                    actors=actors,
                    content={
                        "text": reply_text,
                        "role": "assistant",
                        "tool_executions": tool_executions,
                        "model": creds.model,
                    },
                    source="copilot:assistant",
                    db=db,
                )
        except Exception:
            logger.exception("copilot_persist_assistant_msg_failed")

        return {
            "reply": reply_text,
            "tool_executions": tool_executions,
            "fallback": False,
        }

    @router.post("/scoped/send")
    async def copilot_scoped_send(
        payload: Dict[str, Any],
        request: Request,
        user: Dict[str, Any] = Depends(_require_user),
    ) -> Dict[str, Any]:
        """Scoped single-round-trip dispatch for the schema-assistant and
        dashboard-chat SPA panels (P1, retire-mini-assistants).

        Same transport contract as ``/crm/send`` — auth, creds, model
        override, error handling, return shape — but driven by a
        ``scope`` + ``context`` instead of ``(module, record_id)``. A
        scope preamble is prepended to the message so the agent biases
        to the right tool family (``crm_schema`` / ``crm_dashboard``).

        Decision A: the agent executes directly for admins and proposes
        for non-admins. That admin gating lives downstream in the
        ``crm_schema`` tool (``_actor_is_admin`` → proposal), so this
        route reuses ``/crm/send``'s auth verbatim (``_require_user``)
        and adds no separate confirm protocol.
        """
        scope = str(payload.get("scope") or "").strip()
        if scope not in ("schema", "dashboard", "workflow"):
            raise HTTPException(
                status_code=400, detail="scope must be 'schema', 'workflow' or 'dashboard'",
            )
        ctx = payload.get("context")
        if not isinstance(ctx, dict):
            ctx = {}

        message = str(payload.get("message") or "").strip()
        raw_atts = payload.get("attachments")
        attachments: List[Dict[str, Any]] = (
            [a for a in raw_atts if isinstance(a, dict)]
            if isinstance(raw_atts, list) else []
        )
        if not message and not attachments:
            raise HTTPException(status_code=400, detail="message required")

        # Scope-specific context validation + surface pick + preamble.
        #
        # ``panel_key`` is a NAMESPACED subject module-name used only for
        # persistence. It must NOT collide with the chatbox list-mode
        # (``/crm/send`` with no record_id), which persists subjects as
        # ``[{"module": "<real module name>"}]`` (e.g. ``accounts``). By
        # prefixing with ``ai-schema:`` / ``ai-dashboard:`` we keep the
        # scoped panel timelines in their own bucket — a real CRM module
        # can never be named with a literal colon, so containment /
        # equality queries on the two never overlap.
        if scope in ("schema", "workflow"):
            module_name = str(ctx.get("module") or "").strip()
            if not module_name:
                raise HTTPException(
                    status_code=400, detail=f"context.module required for scope={scope}",
                )
            # 工作流面板复用 schema 助手 surface(同 crm 工具集,含 crm_workflow),
            # 但用独立 panel_key,跟模块管理那边的字段对话分开,互不串扰。
            surface = SurfaceKind.SPA_SCHEMA_ASSISTANT
            if scope == "workflow":
                panel_key = f"ai-workflow:{module_name}"
                preamble = (
                    f"[当前正在『工作流』里管理模块 {module_name} 的自动化。"
                    f"工作流=写事件(记录建/改)/定时→自动执行(算字段/建任务/起草/调 webhook);"
                    f"提醒/扫描/今天该处理 归 coach 不在这里做。用 crm_workflow 工具建/改/启停。] "
                )
            else:
                panel_key = f"ai-schema:{module_name}"
                preamble = (
                    f"[当前正在『模块管理』里编辑模块 {module_name} 的字段结构。"
                    f"用户要改这个模块的 schema(建/改/隐藏字段、建AI字段等),"
                    f"用 crm_schema 工具操作。] "
                )
        else:  # dashboard
            tab_id = str(ctx.get("tab_id") or "").strip()
            if not tab_id:
                raise HTTPException(
                    status_code=400,
                    detail="context.tab_id required for scope=dashboard",
                )
            surface = SurfaceKind.SPA_DASHBOARD_CHAT
            panel_key = f"ai-dashboard:{tab_id}"
            preamble = (
                f"[当前正在看板 tab {tab_id}。用户要改这个看板(建/改图表),"
                f"用 crm_dashboard 工具操作。] "
            )

        # Persisted subject for both turns — the panel-scoped bucket.
        subjects = [{"module": panel_key}]
        actors = [{"type": "user", "id": int(user["id"])}]

        # Resolve LLM creds exactly like /crm/send (model override → 400
        # on invalid; else profile default downstream).
        profile_id = _copilot_profile_id(request, str(user["id"]))
        model_override = payload.get("model")
        from hermes_cloud.app.llm_creds import (
            resolve_for_user, NoProfileModelConfigured, ModelNotInActiveList,
        )
        try:
            creds = resolve_for_user(
                request.app.state.config_store,
                str(user["id"]),
                model_override=model_override,
                profile_id=profile_id,
            )
        except (NoProfileModelConfigured, ModelNotInActiveList) as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }},
            )

        # Persist the user turn first (pre-dispatch), mirroring
        # ``/crm/send``: the question survives even if hermes is down.
        # Charter §定律 1. Stamp attachment filenames into the text so
        # the re-hydrated history shows files were sent.
        persisted_text = message
        if attachments:
            names = "、".join(
                str(a.get("filename") or "file") for a in attachments
            )
            marker = f"📎 {names}"
            persisted_text = f"{message}\n{marker}" if message else marker
        try:
            with db_session() as db:
                emit(
                    kind="chat",
                    subjects=subjects,
                    actors=actors,
                    content={"text": persisted_text, "role": "user"},
                    source="copilot:user",
                    db=db,
                )
        except Exception:
            logger.exception("copilot_scoped_persist_user_msg_failed")

        # No db held across the long dispatch (Charter v2 §一系统).
        envelope = inject_context(
            text=f"{preamble}{message}",
            user_id=int(user["id"]),
            surface=surface,
            record_ref=None,
            attachments=attachments,
        )
        try:
            result = await dispatch_to_hermes(
                app=request.app, envelope=envelope, profile_id=profile_id,
                bearer_key=_bearer(request.app),
                body_extras={"model": creds.model},
                # Multi-step CRM writes regularly exceed the generic 60s budget.
                # Stay below the production reverse proxy's 300s timeout.
                timeout_sec=240.0,
            )
        except Exception as exc:
            logger.exception("copilot_scoped_dispatch_failed: %s", exc)
            return {
                "reply": "",
                "tool_executions": [],
                "fallback": True,
                "error": str(exc),
            }

        reply_text = result.reply_text or ""
        tool_executions = result.tool_executions or []
        if result.status_code >= 400:
            logger.warning(
                "copilot_scoped_dispatch status=%s body_head=%r",
                result.status_code, (result.raw_body or b"")[:200],
            )
        if not reply_text and result.status_code >= 400:
            return {
                "reply": "",
                "tool_executions": tool_executions,
                "fallback": True,
                "error": f"hermes status {result.status_code}",
            }
        if not reply_text:
            reply_text = "（没有产生回复）"

        # Persist the assistant turn (post-dispatch), mirroring
        # ``/crm/send``'s copilot_reply write so the panel re-hydrates.
        try:
            with db_session() as db:
                emit(
                    kind="copilot_reply",
                    subjects=subjects,
                    actors=actors,
                    content={
                        "text": reply_text,
                        "role": "assistant",
                        "tool_executions": tool_executions,
                        "model": creds.model,
                    },
                    source="copilot:assistant",
                    db=db,
                )
        except Exception:
            logger.exception("copilot_scoped_persist_assistant_msg_failed")

        return {
            "reply": reply_text,
            "tool_executions": tool_executions,
            "fallback": False,
        }

    @router.get("/scoped/history")
    async def copilot_scoped_history(
        request: Request,
        scope: str,
        module: Optional[str] = None,
        tab_id: Optional[str] = None,
        limit: int = 40,
        user: Dict[str, Any] = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        """Re-hydrate a scoped AI-assistant panel (schema / dashboard).

        Computes the same namespaced ``panel_key`` the scoped /send
        handler persists under, then re-uses ``copilot_crm_history``'s
        list-mode query/serialization verbatim (exact JSONB equality on
        ``[{"module": panel_key}]``, filtered to the current user's
        actor). Returns ``{turns:[{role,text,at,tool_executions}]}``.

        The ``ai-schema:`` / ``ai-dashboard:`` prefix guarantees these
        rows never collide with chatbox list-mode rows (real module
        names carry no colon).
        """
        if scope in ("schema", "workflow"):
            module_name = str(module or "").strip()
            if not module_name:
                raise HTTPException(
                    status_code=400, detail=f"module required for scope={scope}",
                )
            panel_key = (f"ai-workflow:{module_name}" if scope == "workflow"
                         else f"ai-schema:{module_name}")
        elif scope == "dashboard":
            tid = str(tab_id or "").strip()
            if not tid:
                raise HTTPException(
                    status_code=400, detail="tab_id required for scope=dashboard",
                )
            panel_key = f"ai-dashboard:{tid}"
        else:
            raise HTTPException(
                status_code=400, detail="scope must be 'schema', 'workflow' or 'dashboard'",
            )

        limit = max(1, min(int(limit or 40), 200))
        subj_filter = json.dumps([{"module": panel_key}])
        actor_filter = json.dumps([{"type": "user", "id": int(user["id"])}])
        sql = """
            SELECT id, occurred_at, content, source
              FROM interactions
             WHERE subjects = %s::jsonb
               AND actors @> %s::jsonb
               AND source LIKE 'copilot:%%'
             ORDER BY occurred_at ASC
             LIMIT %s
        """
        params: List[Any] = [subj_filter, actor_filter, limit]
        try:
            rows = db.fetchall(sql, tuple(params)) or []
        except Exception:
            logger.exception(
                "copilot_scoped_history_failed scope=%s key=%s", scope, panel_key,
            )
            raise HTTPException(500, "copilot_scoped_history_failed")

        turns: List[Dict[str, Any]] = []
        for r in rows:
            c = r.get("content")
            if isinstance(c, str):
                try:
                    c = json.loads(c)
                except Exception:
                    c = {}
            if not isinstance(c, dict):
                c = {}
            role = "assistant" if r.get("source") == "copilot:assistant" else "user"
            turn: Dict[str, Any] = {
                "role": role,
                "text": str(c.get("text") or ""),
                "at": r["occurred_at"].isoformat() if r.get("occurred_at") else None,
            }
            tx = c.get("tool_executions")
            if isinstance(tx, list) and tx:
                turn["tool_executions"] = tx
            if role == "assistant" and c.get("model"):
                turn["model"] = c.get("model")
            turns.append(turn)
        return {"turns": turns}

    @router.get("/crm/history")
    async def copilot_crm_history(
        request: Request,
        module: str,
        record_id: Optional[int] = None,
        limit: int = 40,
        user: Dict[str, Any] = Depends(_require_user),
        db: DbWrapper = Depends(get_db),
    ) -> Dict[str, Any]:
        """Re-hydrate the Copilot panel for (module, record_id?).

        Two modes (mirroring /send):
          - record_id given: turns anchored to this specific record.
          - record_id missing: list-mode turns for this user on this
            module (subjects = ``[{"module": <name>}]`` with no id).

        Returns the most recent ``limit`` ``copilot:user`` /
        ``copilot:assistant`` interactions in chronological order,
        packed as ``{turns:[{role,text,at,tool_executions}]}``.
        """
        limit = max(1, min(int(limit or 40), 200))
        if record_id is not None:
            # Record-mode: ``@>`` containment matches subjects that
            # carry both module AND this id.
            subj_filter = json.dumps([{"module": module, "id": int(record_id)}])
            sql = """
                SELECT id, occurred_at, content, source
                  FROM interactions
                 WHERE subjects @> %s::jsonb
                   AND source LIKE 'copilot:%%'
                 ORDER BY occurred_at ASC
                 LIMIT %s
            """
            params: List[Any] = [subj_filter, limit]
        else:
            # List-mode: exact JSONB equality on the no-id subject form
            # so we don't pull in record-mode rows that happen to mention
            # this module. Filtered to the current user's actor so each
            # user only sees their own list-mode chats.
            subj_filter = json.dumps([{"module": module}])
            actor_filter = json.dumps([{"type": "user", "id": int(user["id"])}])
            sql = """
                SELECT id, occurred_at, content, source
                  FROM interactions
                 WHERE subjects = %s::jsonb
                   AND actors @> %s::jsonb
                   AND source LIKE 'copilot:%%'
                 ORDER BY occurred_at ASC
                 LIMIT %s
            """
            params = [subj_filter, actor_filter, limit]
        try:
            rows = db.fetchall(sql, tuple(params)) or []
        except Exception:
            logger.exception(
                "copilot_history_failed module=%s record=%s", module, record_id,
            )
            raise HTTPException(500, "copilot_history_failed")

        turns: List[Dict[str, Any]] = []
        for r in rows:
            c = r.get("content")
            if isinstance(c, str):
                try:
                    c = json.loads(c)
                except Exception:
                    c = {}
            if not isinstance(c, dict):
                c = {}
            role = "assistant" if r.get("source") == "copilot:assistant" else "user"
            turn: Dict[str, Any] = {
                "role": role,
                "text": str(c.get("text") or ""),
                "at": r["occurred_at"].isoformat() if r.get("occurred_at") else None,
            }
            tx = c.get("tool_executions")
            if isinstance(tx, list) and tx:
                turn["tool_executions"] = tx
            if role == "assistant" and c.get("model"):
                turn["model"] = c.get("model")
            turns.append(turn)
        return {"turns": turns}

    app.include_router(router)
