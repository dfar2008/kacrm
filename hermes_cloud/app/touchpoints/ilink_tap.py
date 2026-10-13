"""iLink (wecom_ilink) inbound text dispatch — Phase 4 plan v2.2 §Week 5 Day 1-3.

When a wecom_ilink subscriber sends a text message, the receiver
(``app/channels/wecom.py``) decrypts and normalises it. This module
takes the normalised payload and:

  1. Builds a hermes envelope with ``surface=ILINK`` (patch 27 ACL
     binds the iLink allowlist on the hermes side).
  2. Dispatches via ``proxy.dispatch_to_hermes``.
  3. Renders the reply via ``surface.ilink_text.render_ilink_reply``.
  4. Returns the rendered envelope so the caller pushes it back through
     the wecom send_text API.

Why a helper, not a webhook route: the actual wecom inbound webhook
flow lives in the channels/touchpoints stack and varies per tenant
deployment (SaaS gateway vs self-hosted iLink protocol layer). This
module exposes a stable, testable function so each entry point can
plug in without re-implementing surface + dispatch + render.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# Type aliases — caller injects the actual wecom send_text adapter
# from the place that has the wecom client + access token.
SendText = Callable[[str, str], Awaitable[Any]]
"""``(external_user_id, text) → bridge ack``. Implementer is free to
return whatever the platform yields (message id, OK marker, etc.).
``None``-returning is fine — caller logs."""


async def dispatch_ilink_inbound(
    *,
    text: str,
    external_user_id: str,
    user_id: int,
    profile_id: str,
    profile_manager,
    http_client,
    send_text: Optional[SendText] = None,
    internal_key: str = "",
    timeout_sec: float = 1800.0,
) -> Dict[str, Any]:
    """Forward ``text`` to hermes through the surface layer and either
    return the rendered reply or push it back via ``send_text``.

    Always returns the rendered envelope (``{"kind": "ilink_text",
    "text": ..., "chips": [...]}``) so callers that batch deliveries
    can choose their own push strategy.

    Errors are caught and surfaced as a friendly system message so the
    fire-and-forget caller (typically the tap thread) doesn't crash.
    """
    from hermes_cloud.app.proxy.dispatch import dispatch_to_hermes
    from hermes_cloud.app.surface import SurfaceKind, inject_context
    from hermes_cloud.app.surface.ilink_text import render_ilink_reply

    body = (text or "").strip()
    if not body:
        return {"kind": "ilink_text", "text": "", "chips": []}

    try:
        envelope = inject_context(
            text=body,
            user_id=int(user_id),
            surface=SurfaceKind.ILINK,
        )
        # Patch 20 origin headers for downstream gateway-action routing.
        envelope.headers["X-Hermes-Origin-Channel"] = "wecom_ilink"
        envelope.headers["X-Hermes-Origin-External-User-Id"] = external_user_id

        fake_app = SimpleNamespace(state=SimpleNamespace(
            profile_manager=profile_manager,
            http_client=http_client,
            profile_store=None,
        ))
        logger.info(
            "ilink phase4: dispatch_to_hermes profile_id=%s user_id=%s "
            "session=%s text_head=%r",
            profile_id, user_id, envelope.session_id, body[:40],
        )
        result = await dispatch_to_hermes(
            app=fake_app, envelope=envelope, profile_id=profile_id,
            bearer_key=internal_key, timeout_sec=timeout_sec,
        )
        reply_text = result.reply_text or ""
        if result.status_code >= 400:
            logger.warning(
                "ilink phase4: dispatch status=%s body_head=%r",
                result.status_code, (result.raw_body or b"")[:200],
            )
    except Exception as exc:
        logger.exception("ilink phase4: dispatch failed: %s", exc)
        rendered = render_ilink_reply(
            text=f"⚠️ Agent 暂时不可用：{exc}",
            pending_actions=[],
        )
        if send_text is not None:
            try:
                await send_text(external_user_id, rendered["text"])
            except Exception:
                logger.exception("ilink phase4: failure-notice push also failed")
        return rendered

    if not reply_text:
        reply_text = "(空回复)"
    rendered = render_ilink_reply(text=reply_text, pending_actions=[])

    if send_text is not None:
        try:
            await send_text(external_user_id, rendered["text"])
        except Exception:
            logger.exception("ilink phase4: send_text failed")

    # SoT: emit interaction so the iLink exchange shows in the timeline
    # and feeds the RL pipeline.
    try:
        from hermes_cloud.app.interactions import emit as emit_interaction

        emit_interaction(
            kind="message",
            actors=[{"type": "user", "id": int(user_id)}],
            content={
                "channel": "wecom_ilink",
                "external_user_id": external_user_id,
                "user_message": body,
                "agent_reply": reply_text,
                "profile_id": profile_id,
            },
            source="channel:wecom_ilink",
        )
    except Exception:
        logger.warning("ilink phase4: interaction emit failed", exc_info=True)

    return rendered
