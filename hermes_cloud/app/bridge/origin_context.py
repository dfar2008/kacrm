"""Per-invocation channel origin — Charter §1 渠道闭环 plumbing.

When the celery intake task (or any other invocation entry point) hands
an interaction off to an :class:`AIAgent`, the agent's tool calls go
through hermes' tool dispatcher, which is registered process-wide at
import time and has no clean per-invocation context slot. We want each
``crm.write`` / ``email.send`` / etc. that the agent emits to be
recorded with the originating channel of *this* interaction (Feishu,
WeCom iLink, WebUI, …) so the dispatcher can deliver the approval
prompt back there (see :mod:`hermes.action_gateway.notifiers`).

Threading an explicit ``origin=`` kwarg through every layer
(``run_intake_for_interaction`` → ``agents.base.invoke`` → specialists
→ hermes AIAgent → tool handlers → ``tool_binding.dispatch`` →
``_dispatch_via_gateway``) would mean editing all of them and is
fragile — any layer that forgets to forward loses the origin silently.

Instead we use a :class:`contextvars.ContextVar`:

* The entry point (``run_intake_for_interaction``, the WebUI chat
  endpoint, the Feishu webhook handler, etc.) computes the origin from
  whatever context it has (interaction row's ``actors[*].source`` /
  request body / etc.) and binds it via :func:`bind`.
* Anything inside that scope — including hermes' AIAgent and
  whatever it calls — can read it via :func:`current` without knowing
  the entry point existed.
* Celery tasks each get a fresh context, so origins from one task
  cannot leak into another. WebUI requests likewise (FastAPI runs
  each request inside its own context).
* Default is ``{"channel": "webui"}`` — the only channel that pre-existed
  before this column. A caller that forgets to bind() winds up with
  WebUI origin, which is the safest fallback (the WebUI dashboard
  remains the universal escape hatch).

Usage:

    from hermes_cloud.app.bridge import origin_context

    origin = origin_context.from_interaction_actors(
        actors=row.get("actors"), interaction_id=str(row.get("id")),
    )
    token = origin_context.bind(origin)
    try:
        invoke("intake", ...)
    finally:
        origin_context.reset(token)

Or as a context manager:

    with origin_context.scope(origin):
        invoke("intake", ...)
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar, Token
from typing import Any, Dict, Iterator, List, Optional


_DEFAULT_ORIGIN: Dict[str, Any] = {"channel": "webui"}

_origin_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "hermes_agent_origin", default=None,
)


def bind(origin: Optional[Dict[str, Any]]) -> Token:
    """Set the current invocation origin and return a reset token.

    Pass ``None`` to clear the binding (resolves to default in
    :func:`current`). The returned token must be passed to
    :func:`reset` once the scope exits — prefer :func:`scope` which
    handles that automatically.
    """
    if origin is None:
        return _origin_var.set(None)
    if not isinstance(origin, dict) or not origin.get("channel"):
        # Defensive — never bind a half-formed origin; a downstream
        # ``submit(origin=...)`` would just coerce it back to webui anyway.
        return _origin_var.set(None)
    return _origin_var.set(dict(origin))


def reset(token: Token) -> None:
    """Reset the contextvar binding to whatever it was before
    :func:`bind` returned ``token``."""
    _origin_var.reset(token)


def current() -> Dict[str, Any]:
    """Return the active origin, or the default ``{"channel": "webui"}``.

    Always returns a fresh dict so callers can't mutate the binding by
    accident.
    """
    val = _origin_var.get()
    return dict(val) if val else dict(_DEFAULT_ORIGIN)


@contextlib.contextmanager
def scope(origin: Optional[Dict[str, Any]]) -> Iterator[None]:
    """Bind ``origin`` for the duration of the ``with`` block."""
    token = bind(origin)
    try:
        yield
    finally:
        reset(token)


# ---------------------------------------------------------------------------
# Helpers — derive an origin payload from an ``interactions`` row.
# ---------------------------------------------------------------------------

# Channel name normalization. Touchpoint adapters historically use a
# variety of ``source`` strings; map them to the channel keys the
# notifier registry uses.
_SOURCE_TO_CHANNEL: Dict[str, str] = {
    # WebUI / direct API
    "webui": "webui",
    "chat": "webui",
    "manual": "webui",
    "note": "webui",
    "reclassify": "webui",
    # Channel adapters
    "feishu": "feishu",
    "lark": "feishu",
    "wecom": "wecom_ilink",
    "wecom_ilink": "wecom_ilink",
    "wechat": "wecom_ilink",  # personal WeChat via iLink
    "discord": "discord",
    "telegram": "telegram",
    "slack": "slack",
    "dingtalk": "dingtalk",
    "email": "email",
}


def _normalize_channel(source: str) -> str:
    """Map a touchpoint ``source`` string to a notifier channel key."""
    s = (source or "").strip().lower()
    if not s:
        return "webui"
    if s in _SOURCE_TO_CHANNEL:
        return _SOURCE_TO_CHANNEL[s]
    # Heuristic: ``feishu_card`` → ``feishu``, ``wecom_text`` → ``wecom_ilink``.
    for key, channel in _SOURCE_TO_CHANNEL.items():
        if s.startswith(key + "_") or s.startswith(key + ":"):
            return channel
    return "webui"


def from_interaction_actors(
    *,
    actors: Any,
    interaction_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive an origin payload from an ``interactions.actors`` array.

    Walks the actors list looking for the first user actor with a
    ``source`` field, then folds in any channel-specific external IDs
    that were stored alongside it (``external_user_id`` /
    ``external_message_id`` / ``external_chat_id``). Returns a payload
    safe to pass to :func:`bind` and to ``dispatcher.submit(origin=...)``.

    Falls back to ``{"channel": "webui"}`` if no usable actor is found —
    actions still go on the WebUI dashboard, the operator can clear them
    there.
    """
    payload: Dict[str, Any] = {"channel": "webui"}
    if interaction_id:
        payload["interaction_id"] = str(interaction_id)

    if not isinstance(actors, list):
        return payload

    user_actor: Optional[Dict[str, Any]] = None
    any_actor: Optional[Dict[str, Any]] = None
    for a in actors:
        if not isinstance(a, dict):
            continue
        if any_actor is None and a.get("source"):
            any_actor = a
        if (
            user_actor is None
            and str(a.get("type") or "") == "user"
            and a.get("source")
        ):
            user_actor = a

    src_actor = user_actor or any_actor
    if src_actor is None:
        return payload

    payload["channel"] = _normalize_channel(str(src_actor.get("source") or ""))
    for key in ("external_user_id", "external_message_id", "external_chat_id"):
        v = src_actor.get(key)
        if v not in (None, ""):
            payload[key] = str(v)
    return payload


__all__ = [
    "bind",
    "reset",
    "current",
    "scope",
    "from_interaction_actors",
]
