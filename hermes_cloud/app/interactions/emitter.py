"""``emit()`` — the single write path into the ``interactions`` table.

Everywhere in hermes-cloud that says "something happened" should call
:func:`emit`. The plan doc (``docs/2026-04-15-hermes-crm-implementation-plan.md``
§4.3) lays out the column contract; this module is the canonical writer.

Design notes:

* **Shape-only** — we don't validate ``kind`` / ``source`` against a
  catalog. Callers pick the strings; we store them verbatim. Phase 2's
  feedback collector + RL sample builder filter by substring.
* **JSONB columns accept Python dicts/lists** — psycopg3 serialises them
  automatically when the backing column is ``jsonb``.
* **``subjects`` is always a list of ``{"module": str, "id": int}``** for
  CRM writes; other producers (feishu messages, workflow completions)
  may use ``{"type": ..., ...}`` shapes. The GIN index keeps lookup fast
  either way.
* **Best-effort, not transactional w/ the business write** — if the
  business code is inside an outer transaction the emit joins it (we
  reuse the caller's DbWrapper). If not, emit opens its own short
  connection. Phase 2's gateway will tighten this to "same txn always".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hermes.crm.db import DbWrapper, db_session


_JSONB_COLUMNS = ("actors", "subjects", "content", "provenance")


def _to_jsonb(value: Any) -> Any:
    """psycopg3 only auto-adapts dict/list → jsonb when you pass ``Json(...)``
    or use the ``jsonb`` column explicitly. The simplest cross-version path
    is to pre-encode to a JSON string and cast in SQL via ``::jsonb``.
    """
    if value is None:
        return None
    return json.dumps(value, default=_json_fallback, ensure_ascii=False)


def _json_fallback(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    # A stray set/frozenset (e.g. a field-name diff) must not blow up the
    # interactions JSONB write — coerce to a deterministic list rather than
    # rolling back the whole business write. Mirrors the set-safe Celery
    # serializer in hermes_cloud.app.celery_app.
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(
            part for part in (_flatten_text(item) for item in value) if part
        ).strip()
    if isinstance(value, dict):
        return " ".join(
            part for part in (_flatten_text(item) for item in value.values()) if part
        ).strip()
    return ""


def _extract_search_text(content: Optional[Dict[str, Any]]) -> str:
    if not content:
        return ""
    parts: List[str] = []
    for key in ("summary", "text", "body", "subject", "message"):
        part = _flatten_text(content.get(key))
        if part:
            parts.append(part)
    return " ".join(parts).strip()


def _extract_profile_id(actors: Optional[List[Dict[str, Any]]]) -> str:
    if not actors:
        return ""
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        profile_id = actor.get("profile_id")
        if profile_id not in (None, ""):
            return str(profile_id)
    return ""


# ---------------------------------------------------------------------------
# kind="agent.message" — Charter §一/§二 contract for agent conversation events.
# ---------------------------------------------------------------------------
# See docs/2026-04-25-agent-message-as-event.md for the full schema. The
# validator below is the gatekeeper at the canonical write path; any caller
# that tries to emit an ``agent.message`` row missing the required shape will
# be rejected so the Interaction Log stays a usable training corpus.

_AGENT_MESSAGE_REQUIRED_CONTENT = ("session_id", "message_id", "role", "text")
_AGENT_MESSAGE_VALID_ROLES = ("assistant", "user", "tool", "system")


def _validate_agent_message(
    *,
    actors: Optional[List[Dict[str, Any]]],
    content: Optional[Dict[str, Any]],
) -> None:
    """Reject malformed ``kind="agent.message"`` rows.

    Charter §一定律 1: events are first-class. An agent-message row that
    drops session_id or message_id can't be re-stitched into a conversation
    later, so emit() refuses up-front rather than letting a half-event into
    the Log.
    """
    if not isinstance(content, dict):
        raise ValueError(
            "agent.message requires a content dict; "
            "see docs/2026-04-25-agent-message-as-event.md"
        )
    missing = [k for k in _AGENT_MESSAGE_REQUIRED_CONTENT
               if not content.get(k) and content.get(k) != 0]
    # ``role`` must additionally be one of the known values so downstream
    # consumers (memory.ingest, RL sample builder) can switch on it.
    if "role" not in missing and content.get("role") not in _AGENT_MESSAGE_VALID_ROLES:
        missing.append(f"role (got {content.get('role')!r}, "
                       f"want one of {_AGENT_MESSAGE_VALID_ROLES})")
    if missing:
        raise ValueError(
            f"agent.message content missing/invalid: {missing}; "
            "see docs/2026-04-25-agent-message-as-event.md"
        )

    # tool result rows MUST carry tool_call_id so RL / the SPA can pair
    # "this is the tool I asked to run" (the assistant row's ``tool_calls``)
    # with "this is what came back" (this row). ``tool_name`` is NOT
    # required: by message-format convention a tool-result message carries
    # only tool_call_id, not the name — the name lives on the paired
    # assistant tool_call row. Requiring it here rejected every legitimate
    # tool result and dropped it from the Interaction Log.
    if content.get("role") == "tool" and not content.get("tool_call_id"):
        raise ValueError(
            "agent.message role=tool requires tool_call_id; "
            "without it the call/result pair can't be stitched."
        )

    # Actors must include an agent actor so we know which specialist
    # produced this — RL training keys on actors[agent].name.
    actors_list = actors or []
    has_agent = any(
        isinstance(a, dict) and str(a.get("type") or "") == "agent"
        and str(a.get("name") or "").strip()
        for a in actors_list
    )
    if not has_agent:
        raise ValueError(
            "agent.message requires actors include "
            '{"type":"agent","name":"<agent_name>"}; '
            "see docs/2026-04-25-agent-message-as-event.md"
        )


def emit(
    *,
    kind: str,
    subjects: Optional[List[Dict[str, Any]]] = None,
    actors: Optional[List[Dict[str, Any]]] = None,
    content: Optional[Dict[str, Any]] = None,
    source: str = "manual",
    occurred_at: Optional[datetime] = None,
    draft_id: Optional[int] = None,
    derived_from: Optional[str] = None,
    provenance: Optional[Dict[str, Any]] = None,
    db: Optional[DbWrapper] = None,
) -> str:
    """Insert one row into ``interactions`` and return its UUID.

    Parameters
    ----------
    kind:
        Taxonomy token — ``'action'`` for CRM writes, ``'email'`` /
        ``'call'`` / ``'meeting'`` / ``'message'`` / ``'doc'`` for
        communication events.
    subjects:
        List of record references, e.g. ``[{"module": "accounts", "id": 17}]``.
    actors:
        List of actor references, e.g. ``[{"type": "user", "id": 1}]``.
    content:
        Payload body — for a CRM write typically ``{"op": "update",
        "changed_fields": [...]}``; for messages the raw body lives here.
    source:
        Provenance string — ``'manual'``, ``'agent:<profile>:<agent>'``,
        ``'workflow'``, ``'channel:feishu'``, etc.
    occurred_at:
        Business timestamp (when the thing happened). Defaults to now.
    draft_id:
        ``rl_agent_drafts.id`` when the row originates from an agent draft.
    derived_from:
        ``interactions.id`` of the parent event (e.g. a call's auto-summary
        derives from the call transcript row).
    provenance:
        ``{"privacy_level": ..., "confidence": ..., "redacted": ...}``.
    db:
        Optional existing :class:`DbWrapper`; joins the caller's txn if
        supplied. Omit to open a short-lived session.

    Returns
    -------
    str
        The new interaction's UUID as a string.
    """
    if kind == "agent.message":
        _validate_agent_message(actors=actors, content=content)

    ts = occurred_at or datetime.now(tz=timezone.utc)
    search_text = _extract_search_text(content)

    sql = (
        "INSERT INTO interactions "
        "(kind, occurred_at, actors, subjects, content, source, "
        " draft_id, derived_from, provenance, search_text) "
        "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb, %s) "
        "RETURNING id"
    )
    params = (
        kind,
        ts,
        _to_jsonb(actors),
        _to_jsonb(subjects),
        _to_jsonb(content),
        source,
        draft_id,
        derived_from,
        _to_jsonb(provenance),
        search_text,
    )

    def _do(handle: DbWrapper) -> str:
        row = handle.fetchone(sql, params)
        if not row or "id" not in row:
            raise RuntimeError("interactions insert returned no id")
        # fetchone() does not call _maybe_commit, so an INSERT driven through
        # it leaves the row uncommitted on the connection. When the caller's
        # db is a FastAPI request-scoped DbWrapper the pool's return path
        # can drop the pending write, so we commit explicitly here to match
        # DbWrapper.execute/insert's default commit=True semantics. If the
        # caller holds an open transaction (depth>0), _maybe_commit no-ops.
        # pylint: disable=protected-access
        handle._maybe_commit(True)
        return str(row["id"])

    def _route(interaction_id: str) -> None:
        try:
            from hermes_cloud.app.interactions.router import route_new_interaction

            route_new_interaction(
                interaction_id=interaction_id,
                kind=kind,
                profile_id=_extract_profile_id(actors),
            )
        except Exception as exc:
            # Routing is best-effort and must never break write path.
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "route_new_interaction_failed id=%s kind=%s err=%r",
                interaction_id, kind, exc,
            )

    if db is not None:
        interaction_id = _do(db)
        # Transaction visibility rule: when caller provides db we may be
        # inside an open transaction, so we cannot route synchronously —
        # the celery worker would race the not-yet-committed row and
        # ``run_intake_for_interaction`` would see "interaction not found".
        # Defer until after the caller's commit via DbWrapper.after_commit
        # (added in v2.3 — falls back to a debug log + no-op if the wrapper
        # is a test stub without the hook).
        if hasattr(db, "after_commit"):
            db.after_commit(lambda iid=interaction_id: _route(iid))
        else:  # pragma: no cover — defensive for non-DbWrapper test doubles
            import logging as _lg
            _lg.getLogger(__name__).debug(
                "emit_skip_route_no_after_commit id=%s kind=%s",
                interaction_id, kind,
            )
        return interaction_id
    with db_session() as new_db:
        interaction_id = _do(new_db)
    _route(interaction_id)
    return interaction_id
