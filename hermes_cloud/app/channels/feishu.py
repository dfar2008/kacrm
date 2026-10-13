"""Feishu webhook receiver + Action Gateway pending-approval notifier.

Per the per-user-channel-bot model, Feishu *inbound* events run
natively inside each profile's hermes subprocess (its own
``FeishuAdapter`` long connection) — the cloud owns no Feishu bot and
no shared websocket. This module is two small cloud-side pieces:

  * :class:`FeishuReceiver` — the optional webhook transport for
    deployments that subscribe Feishu events to an HTTP callback;
  * :func:`_feishu_pending_notifier` — routes Action Gateway approval
    prompts back to the user via their own per-profile subprocess.

Webhook transport (``POST /api/channels/feishu/callback``) is
optional and follows the spec at
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event/event-subscription-configure-/event-callback/encrypt-event-and-decrypt-it:

    signature = sha1(encrypt_key + timestamp + nonce + encrypt_body)

URL-verification challenge: first request posts
``{"challenge": "...", "type": "url_verification", "token": "..."}``;
reply with ``{"challenge": same}`` (and match ``token`` against config).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from hermes_cloud.app.channels.base import (
    ChannelConfig, ChannelReceiver, ChannelVerificationError,
    constant_time_equals, sha1_hex, register_receiver,
)
from hermes_cloud.app.channels.dingtalk import _decrypt_aes_cbc

logger = logging.getLogger(__name__)


class FeishuReceiver(ChannelReceiver):
    source = "channel:feishu"
    touchpoint_source = "channel:feishu"

    def load_config(self, cfg: Any) -> ChannelConfig:
        enabled = bool(getattr(cfg, "feishu_enabled", False))
        data = {
            "verification_token": (
                getattr(cfg, "feishu_verification_token", None) or ""
            ),
            "encrypt_key": getattr(cfg, "feishu_encrypt_key", None) or "",
        }
        return ChannelConfig(enabled=enabled, data=data)

    def challenge_response(self, *, body, query, cfg):
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        # Feishu sometimes encrypts even the challenge — decrypt first.
        if isinstance(data, dict) and data.get("encrypt"):
            enc = data["encrypt"]
            aes_key = cfg.get("encrypt_key")
            if not aes_key:
                return None  # plaintext path will catch it
            plain = _decrypt_aes_cbc(aes_key, enc)
            try:
                data = json.loads(plain.decode("utf-8"))
            except Exception:
                return None
        if isinstance(data, dict) and data.get("type") == "url_verification":
            token = cfg.get("verification_token")
            if token and not constant_time_equals(
                str(data.get("token") or ""), str(token),
            ):
                raise ChannelVerificationError("feishu verification_token mismatch")
            return {"challenge": data.get("challenge", "")}
        return None

    def verify_and_extract(self, *, headers, body, query, cfg):
        # Check challenge before signature; url-verification posts may
        # arrive before the encrypt_key is active.
        ch = self.challenge_response(body=body, query=query, cfg=cfg)
        if ch is not None:
            return None

        aes_key = cfg.get("encrypt_key")
        try:
            envelope = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ChannelVerificationError(f"feishu body not JSON: {exc}")

        # Plaintext events (encrypt_key not configured or disabled)
        if "encrypt" not in envelope:
            # v2 header-based signature
            token_hdr = headers.get("x-lark-request-token") or ""
            cfg_token = cfg.get("verification_token")
            if cfg_token and not constant_time_equals(cfg_token, token_hdr):
                # v1 schema doesn't carry the header; accept only if no
                # token configured.
                raise ChannelVerificationError("feishu request-token mismatch")
            return envelope

        # Encrypted — verify sha1 signature
        if not aes_key:
            raise ChannelVerificationError("feishu encrypt_key required for encrypted events")
        ts = headers.get("x-lark-request-timestamp") or ""
        nonce = headers.get("x-lark-request-nonce") or ""
        sig = headers.get("x-lark-signature") or ""
        if ts and nonce and sig:
            expected = sha1_hex(aes_key, ts, nonce, envelope["encrypt"])
            if not constant_time_equals(expected, sig):
                raise ChannelVerificationError("feishu sha1 signature mismatch")
        plain = _decrypt_aes_cbc(aes_key, envelope["encrypt"])
        try:
            return json.loads(plain.decode("utf-8"))
        except Exception as exc:
            raise ChannelVerificationError(f"feishu decrypted not JSON: {exc}")


register_receiver(FeishuReceiver())


# ---------------------------------------------------------------------------
# Pending-approval notifier (Charter §1 渠道闭环)
# ---------------------------------------------------------------------------

def _feishu_pending_notifier(action: Dict[str, Any], origin: Dict[str, Any]) -> None:
    """Deliver an Action Gateway approval prompt to Feishu.

    Charter §1 routes pending approvals back to the channel that
    originated them. The prompt is delivered through the user's OWN
    per-profile hermes subprocess (``/internal/channel/push`` → native
    FeishuAdapter using that profile's ``config.yaml`` credentials) —
    the same per-user-bot path every other channel uses; the cloud owns
    no Feishu bot. ``origin`` holds the open_id to DM; without it we
    bail loudly and the operator clears the action from the WebUI
    dashboard, so the action itself is not lost.

    Errors are swallowed by the notifier registry, so a transient
    Feishu outage will not crash the dispatcher.
    """
    from hermes_cloud.app.channels.wecom import build_gateway_approval_text
    from hermes_cloud.app.insights.notify import _post_via_cloud

    open_id = str(origin.get("external_user_id") or "").strip()
    if not open_id:
        logger.warning(
            "feishu pending notifier: missing external_user_id in origin "
            "(action=%s) — falling back to webui dashboard",
            action.get("id"),
        )
        return

    profile_id = str(action.get("profile_id") or "").strip()
    if not profile_id:
        logger.warning(
            "feishu pending notifier: missing profile_id (action=%s)",
            action.get("id"),
        )
        return
    try:
        user_id_for_log = int(profile_id) if profile_id.isdigit() else 0
    except Exception:
        user_id_for_log = 0

    text = build_gateway_approval_text(action)
    ok = _post_via_cloud(
        user_id=user_id_for_log,
        profile_id=profile_id,
        platform="feishu",
        channel_id=open_id,
        text=text,
    )
    if not ok:
        logger.warning(
            "feishu pending notifier: text POST failed action=%s open_id=%s",
            action.get("id"), open_id,
        )


def _register_pending_notifier() -> None:
    """Register the feishu pending notifier with the Action Gateway."""
    try:
        from hermes.action_gateway import notifiers
        notifiers.register("feishu", _feishu_pending_notifier)
    except Exception:
        logger.exception("feishu: pending_notifier registration failed")


_register_pending_notifier()


# ---------------------------------------------------------------------------
# Long-connection tap
# ---------------------------------------------------------------------------

__all__ = ["FeishuReceiver"]
