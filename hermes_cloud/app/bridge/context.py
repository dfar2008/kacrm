"""Bridge request context — per-request identity + profile + provenance.

Phase 1e (plan §6.2). A single FastAPI dep that every hermes and CRM
route can pull in to get a uniform view of "who is making this request,
which profile do they have active, and what's the provenance stamp we
should attach to any downstream writes". Compared to calling
``bridge.identity.current_user`` directly, :class:`RequestContext`:

1. Resolves the user's default profile from ``app.state.profile_store``
   in one extra round-trip — so routes don't each have to re-derive it.
2. Normalises the ``source`` provenance stamp (``'manual'`` for humans,
   ``'agent:<profile>:<agent>'`` for Agent-driven writes, later
   ``'feishu:<msg_id>'`` etc.) that
   :class:`hermes.crm.save.Save` writes into
   ``interactions.source``. Centralising this in the dep means CRM
   /v1 parity — both entry points produce the same audit trail.

Phase 2+ will extend :class:`RequestContext` with fields the plan
sketched (``active_agent``, ``current_record``, ``trust_snapshot``) —
they're elided here because none of the downstream consumers exist
yet. Adding them speculatively would force the dep to make queries
on every request that nothing would read. See plan §6.2 for the
target shape.

Source provenance:
  * ``X-Source`` request header wins if present (callers like the
    Agent gateway set it explicitly). Value must start with
    ``'agent:'`` / ``'feishu:'`` / ``'system:'`` — unknown prefixes
    fall back to ``'manual'`` so a malicious client can't forge
    provenance.
  * Otherwise ``'manual'`` — the kacrm-compatible default that
    :class:`Save` already uses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from hermes_cloud.app.bridge.identity import current_user as bridge_current_user


logger = logging.getLogger(__name__)


# Allowed source prefixes — anything outside this set resolves to
# ``'manual'``. Keep this tight; widening the set is a security review.
_ALLOWED_SOURCE_PREFIXES: tuple[str, ...] = (
    "manual",
    "agent:",
    "feishu:",
    "system:",
)


def _resolve_source(request: Request) -> str:
    raw = request.headers.get("X-Source", "").strip()
    if not raw:
        return "manual"
    lowered = raw.lower()
    for prefix in _ALLOWED_SOURCE_PREFIXES:
        if lowered == prefix.rstrip(":") or lowered.startswith(prefix):
            return raw
    logger.warning("context_source_rejected value=%r", raw)
    return "manual"


@dataclass
class RequestContext:
    """Per-request identity + profile + provenance.

    ``user_id`` follows the :class:`AuthStore` protocol-level
    decimal-string convention (``"1"``, ``"17"``). ``profile_id``
    matches the current :class:`ProfileStore` id shape (VARCHAR until
    Phase 1c.3b flips it to BIGSERIAL). ``source`` is a lowercase
    provenance stamp written verbatim into ``interactions.source`` by
    :class:`Save`.

    ``is_admin`` is hoisted from :class:`UserAccount` for ergonomic
    access — a *lot* of route code reads it and having it here saves
    the extra ``request.state.user`` lookup.

    Future fields (Phase 2+):
      * ``active_agent``: ``'profile_slug/agent_name'`` of the running
        Agent when this request originates from the Agent Pool.
      * ``current_record``: ``{module, id}`` the frontend says the
        user is currently "looking at" — feeds Agent context.
      * ``trust_snapshot``: per-Agent action-kind trust levels.
    """

    user_id: str
    is_admin: bool
    profile_id: Optional[str]
    source: str
    # Placeholder for Phase 2 — keeps the dataclass forward-compatible
    # without forcing downstream code to handle missing attributes.
    extra: dict[str, Any] = field(default_factory=dict)


def get_context(request: Request) -> RequestContext:
    """FastAPI dep: build a :class:`RequestContext` for *request*.

    Raises ``HTTPException(401)`` when the caller isn't authenticated —
    :func:`bridge.identity.current_user` is the gate. Unauthenticated
    routes shouldn't take this dep; use :func:`try_get_context` for
    best-effort resolution (currently unused — add when a route needs
    it).
    """
    user = bridge_current_user(request)
    profile_id: Optional[str] = None
    profile_store = getattr(request.app.state, "profile_store", None)
    if profile_store is not None:
        try:
            p = profile_store.get_default_profile(user_id=user.id)
            if p is not None:
                profile_id = p.id
        except Exception as exc:  # pragma: no cover — defensive
            # Non-fatal: profile_store outage shouldn't break the
            # request, the caller just won't get a profile_id.
            logger.warning(
                "get_context_profile_lookup_failed uid=%s err=%r",
                user.id,
                exc,
            )
    return RequestContext(
        user_id=user.id,
        is_admin=bool(user.is_admin),
        profile_id=profile_id,
        source=_resolve_source(request),
    )


__all__ = ["RequestContext", "get_context"]
