"""Request-scoped locale resolution middleware.

The CRM i18n layer (``hermes.crm.i18n``) uses a ``ContextVar``
to hold the current locale so helpers like ``localize_field_label``
don't have to pass ``request`` through every call. Without a middleware
to populate that ContextVar per request, the default (``zh-CN``) would
be the only value anyone ever sees — so ``?locale=en-US`` / the
``Accept-Language`` header / session-stored preferences would all be
silently ignored.

Resolution order (matches ``resolve_locale`` in
``crm/i18n/service.py``): ``?locale=`` or ``?lang=`` query > session >
``Accept-Language`` header > ``zh-CN`` default.

Implemented as a **pure ASGI middleware** (not
``BaseHTTPMiddleware``) on purpose. ``BaseHTTPMiddleware`` spawns a
separate task for ``call_next``, which creates a fresh ContextVar
context — so a ``set_current_locale()`` call in the middleware would
be invisible to the route handler running in that child task. Pure
ASGI middleware stays on the same task and therefore the same
ContextVar context throughout the request lifecycle.

Placement: register **after** SessionMiddleware so ``request.session``
is populated when ``resolve_locale`` inspects it. Starlette applies
middleware in reverse registration order, so SessionMiddleware
(registered first) wraps I18nMiddleware (registered second) at
runtime — the request flows Session → I18n → route.
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hermes.crm.i18n.service import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    resolve_locale,
    set_current_locale,
)


def _normalize_x_locale(raw: str) -> str:
    """Normalize an ``X-Locale`` header value to a SUPPORTED_LOCALES key.

    Accepts ``zh``/``zh-cn``/``zh_CN``/``zh-CN``. Returns ``""`` when
    the value can't be matched — caller falls back to ``resolve_locale``.
    """
    v = (raw or "").strip().replace("_", "-")
    if not v:
        return ""
    # Exact match (case-insensitive on the region tag).
    for sup in SUPPORTED_LOCALES:
        if v.lower() == sup.lower():
            return sup
    # Language-only match (``zh`` → ``zh-CN``).
    base = v.split("-", 1)[0].lower()
    for sup in SUPPORTED_LOCALES:
        if sup.lower().startswith(base):
            return sup
    return ""


class I18nMiddleware:
    """Populate the ``current_locale`` ContextVar per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Build a Starlette Request so ``resolve_locale`` can read
        # query_params, headers, and the session populated by the
        # upstream SessionMiddleware.
        request = Request(scope)
        # SPA-explicit signal first: every authenticated SPA request
        # carries the user's current pick as ``X-Locale``. This bypasses
        # session-stored locale, which can go stale (e.g. user toggles
        # zh → en → reload-without-toggle: session still says ``en-US``
        # so resolve_locale returns en-US even though localStorage
        # already flipped back to zh-CN). The header reflects the SPA's
        # current state of truth on every call, so backend never serves
        # stale-locale picklist labels / field labels.
        picked = ""
        try:
            picked = _normalize_x_locale(
                request.headers.get("x-locale") or "",
            )
        except Exception:
            picked = ""
        if not picked:
            try:
                picked = resolve_locale(request)
            except Exception:
                picked = DEFAULT_LOCALE
        set_current_locale(picked)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-locale", picked.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
