"""Touchpoints REST API.

Mounted at ``/api/touchpoints``. Three surfaces:

- ``POST /api/touchpoints/note``         — manual contact log (replaces
                                            kacrm's ``notes`` save path)
- ``POST /api/touchpoints/chat``         — chat-box turn → interaction
- ``POST /api/touchpoints/ingest/{src}`` — adapter-routed ingest for
                                            every channel (feishu /
                                            wecom / wechat / dingtalk /
                                            slack / telegram / discord /
                                            email / webhook)
- ``GET  /api/touchpoints/``             — list registered adapters

Every handler is a thin wrapper around :func:`touchpoints.base.ingest`.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from hermes_cloud.app.auth import require_dashboard_auth
from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.touchpoints.base import (
    get_adapter, ingest, list_adapters,
)


def _require_internal_key(request: Request) -> None:
    """Validate X-Internal-Key header against cfg.internal_key.

    Charter v2 §一系统：同机器内部进程互调不需要 key —— loopback
    (127.0.0.1 / ::1) 直接放行；远端调用（webhook 等）才校验 key.

    Used by the hermes gateway → hermes_cloud tap: the gateway runs in
    a different process (per-user agent runtime) and reports inbound
    IM messages back into the Interaction Log via this header.
    """
    peer = (request.client.host if request.client else "") or ""
    if peer in ("127.0.0.1", "::1", "localhost"):
        return
    config = getattr(request.app.state, "config", None) or AppConfig()
    secret = getattr(config, "channel_bind_webhook_secret", None) or config.internal_key
    provided = request.headers.get("X-Internal-Key", "")
    if not secret or provided != secret:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid internal key.",
                              "type": "auth_error",
                              "code": "invalid_internal_key"}},
        )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/touchpoints", tags=["touchpoints"])


def _auth(request: Request) -> dict:
    """Resolve the dashboard session to a ``{"id": int}`` shape.

    ``require_dashboard_auth`` returns ``(user_id, token)`` — a raw tuple.
    Our handlers historically read ``user["id"]``, so normalise to a dict
    here so every ``/api/touchpoints/*`` endpoint gets the same shape.
    """
    config = getattr(request.app.state, "config", None) or AppConfig()
    raw = require_dashboard_auth(request, config)
    if isinstance(raw, tuple):
        uid = int(raw[0]) if raw and raw[0] is not None else 0
        return {"id": uid}
    if isinstance(raw, dict):
        return raw
    return {"id": int(raw) if raw else 0}


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return data


# Slug-shaped placeholders the dashboard sends when the active profile
# isn't known client-side — we treat these as "look up the user's default".
_PROFILE_ID_PLACEHOLDERS = {"", "default", "main", "auto", "none"}


def _resolve_profile_id(user_id: int, body: Dict[str, Any]) -> str:
    """Resolve ``body['profile_id']`` to a real ``profiles.id`` string.

    The dashboard chat box and several CLI clients pass ``profile_id="default"``
    (a slug, not the actual id). Downstream tables — notably
    ``rl_agent_drafts.profile_id`` — FK to ``profiles.id``, so the empty /
    slug-shaped value used to silently fall through and the async intake
    task would crash with a foreign-key violation (see
    ``docs/2026-04-21-agent-pool-v2.2-bigbang-implementation-manual.md``
    P8-S3 e2e finding).

    Resolution rules, in order:

    1. If ``body['profile_id']`` already looks like a real id (non-empty,
       not in the placeholder set), keep it.
    2. Otherwise look up the user's default profile.
    3. If neither yields a value, return "" — caller decides whether to
       proceed or error. Most touchpoint paths will still emit; only the
       async-intake handoff requires a real id.
    """
    raw = str(body.get("profile_id") or "").strip()
    if raw and raw.lower() not in _PROFILE_ID_PLACEHOLDERS:
        return raw

    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            # v2: profiles has no user_id column; JOIN to users.
            row = db.fetchone(
                "SELECT p.id FROM profiles p "
                "JOIN users u ON u.username = p.id "
                "WHERE u.id = %s AND p.is_active = TRUE "
                "ORDER BY p.is_default DESC, p.id ASC LIMIT 1",
                (int(user_id),),
            )
        if row and row.get("id"):
            return str(row["id"])
    except Exception as exc:
        logger.warning(
            "touchpoint_profile_resolve_failed user_id=%s err=%r",
            user_id, exc,
        )
    return ""


@router.get("/")
def list_touchpoints(request: Request):
    _auth(request)
    return {"adapters": list_adapters()}


@router.post("/note")
async def post_note(request: Request):
    """Manual contact log — the "add 联系记录" form posts here.

    Replaces kacrm's ``notes`` insert path. Writes one row into
    ``interactions`` with ``kind='contact_log'``. The four
    activity-summary fields on the account/contact/potential are
    computed at read time by ``crm.projections.contact_stats``.
    """
    user = _auth(request)
    body = await _body(request)
    body.setdefault("user_id", int(user["id"]))
    body.setdefault("kind", "contact_log")
    body["profile_id"] = _resolve_profile_id(int(user["id"]), body)
    try:
        interaction_id = ingest("manual", body)
    except Exception as exc:
        logger.exception("touchpoint_note_failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": {"interaction_id": interaction_id}}


@router.post("/chat")
async def post_chat(request: Request):
    """Chat-box turn — records user's message against the active record."""
    user = _auth(request)
    body = await _body(request)
    body.setdefault("user_id", int(user["id"]))
    body.setdefault("role", "user")
    body["profile_id"] = _resolve_profile_id(int(user["id"]), body)
    try:
        interaction_id = ingest("chat", body)
    except Exception as exc:
        logger.exception("touchpoint_chat_failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": {"interaction_id": interaction_id}}


@router.post("/reclassify")
async def post_reclassify(request: Request):
    """Create a derived interaction with a new kind.

    Body::
        {
            "derived_from": "<interaction_uuid>",
            "new_kind": "call_log",
            "channel": "phone",
            "summary": "..."
        }
    """
    user = _auth(request)
    body = await _body(request)
    derived_from = str(body.get("derived_from") or "").strip()
    new_kind = str(body.get("new_kind") or "").strip()
    if not derived_from or not new_kind:
        raise HTTPException(
            status_code=400,
            detail="derived_from and new_kind are required",
        )

    from hermes.crm.db import db_session

    with db_session() as db:
        parent = db.fetchone(
            "SELECT id, kind, subjects FROM interactions WHERE id = %s",
            (derived_from,),
        )
    if not parent:
        raise HTTPException(status_code=404, detail="parent interaction not found")

    body.setdefault("user_id", int(user["id"]))
    body.setdefault("subjects", parent.get("subjects") or [])
    body.setdefault("from_kind", parent.get("kind"))

    try:
        interaction_id = ingest("reclassify", body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("touchpoint_reclassify_failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": {"interaction_id": interaction_id}}


@router.post("/pii_view")
async def post_pii_view(request: Request):
    """Charter Law 6 — 审计作为架构原语.

    Every time a human unmasks a PII field (email / phone / id_card / ...)
    we emit a ``kind='pii_view'`` interaction pinning who looked at what
    and when. The raw value is NEVER written to the log — only the field
    name — so the audit log itself doesn't become a PII exfiltration vector.

    Body::

        {
            "module": "contacts",
            "record_id": 73,
            "field": "mobile"
        }
    """
    user = _auth(request)
    user_id = int(user["id"])
    body = await _body(request)

    field = str(body.get("field") or "").strip()
    if not field:
        raise HTTPException(status_code=400, detail="field is required")

    subjects: list[dict] = []
    module = body.get("module")
    record_id = body.get("record_id")
    if module and record_id is not None:
        try:
            subjects.append({"module": str(module), "id": int(record_id)})
        except (TypeError, ValueError):
            pass

    from hermes_cloud.app.interactions import emit as emit_interaction
    from hermes.crm.db import db_session

    try:
        with db_session() as db:
            iid = emit_interaction(
                kind="pii_view",
                subjects=subjects or None,
                actors=[{"type": "user", "id": user_id}],
                content={"field": field},
                source="manual:pii_view",
                db=db,
            )
    except Exception as exc:
        logger.exception("touchpoint_pii_view_failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": {"interaction_id": iid}}


@router.post("/internal/ingest/{source}")
async def post_internal_ingest(request: Request, source: str):
    """Gateway → cloud tap for inbound IM messages.

    Same adapter routing as ``/ingest/{source}`` but authenticated via
    ``X-Internal-Key`` instead of a dashboard session. Used by
    ``hermes/gateway/platforms/*`` to copy every received message into
    the Interaction Log without needing a per-user dashboard session.
    """
    _require_internal_key(request)
    body = await _body(request)

    key = source if source.startswith(("channel:", "webhook")) else f"channel:{source}"
    adapter = get_adapter(key) or get_adapter(source)
    if adapter is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown touchpoint source: {source!r}. "
                   f"Known: {list_adapters()}",
        )

    try:
        interaction_id = adapter.ingest(body)
    except Exception as exc:
        logger.exception("touchpoint_internal_ingest_failed source=%s", source)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": {"interaction_id": interaction_id, "source": adapter.source}}


@router.post("/ingest/{source}")
async def post_ingest(request: Request, source: str):
    """Adapter-routed ingest.

    ``source`` is either a bare key (``feishu``, ``wecom``, ...) or
    a fully-qualified registry name (``channel:feishu``). The handler
    expects the payload already to have passed its channel-specific
    signature verification — mounting that verification is the job
    of the caller (channel worker, webhook proxy, etc.).
    """
    _auth(request)
    body = await _body(request)

    key = source if source.startswith(("channel:", "webhook")) else f"channel:{source}"
    adapter = get_adapter(key) or get_adapter(source)
    if adapter is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown touchpoint source: {source!r}. "
                   f"Known: {list_adapters()}",
        )

    try:
        interaction_id = adapter.ingest(body)
    except Exception as exc:
        logger.exception("touchpoint_ingest_failed source=%s", source)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": {"interaction_id": interaction_id, "source": adapter.source}}
