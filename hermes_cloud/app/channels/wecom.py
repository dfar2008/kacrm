"""WeCom (企业微信) receiver.

WeCom event callbacks always come encrypted (AES-CBC) with a SHA1
signature over token + timestamp + nonce + encrypt_b64.

URL-verification flow: on first configuration WeCom POSTs an ``echostr``
that must be decrypted and echoed back in plaintext.

Spec: https://developer.work.weixin.qq.com/document/path/90968
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from hermes_cloud.app.channels.base import (
    ChannelConfig, ChannelReceiver, ChannelVerificationError,
    constant_time_equals, sha1_hex, register_receiver,
)
# Reuse DingTalk's identical AES-CBC unpacker — same scheme.
from hermes_cloud.app.channels.dingtalk import _decrypt_aes_cbc

logger = logging.getLogger(__name__)


def _parse_xml_or_json(body: bytes) -> dict:
    """WeCom historically uses XML but newer apps can use JSON."""
    text = body.decode("utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        import json
        return json.loads(text)
    # XML fallback — minimal parser: grab <Encrypt>...</Encrypt> etc.
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except Exception as exc:
        raise ChannelVerificationError(f"wecom body not XML/JSON: {exc}")
    return {child.tag: (child.text or "") for child in root}


class WeComReceiver(ChannelReceiver):
    source = "channel:wecom"
    touchpoint_source = "channel:wecom"
    needs_decryption = True

    def load_config(self, cfg: Any) -> ChannelConfig:
        data = {
            "token": getattr(cfg, "wecom_token", None) or "",
            "aes_key": getattr(cfg, "wecom_encoding_aes_key", None) or "",
            "corp_id": getattr(cfg, "wecom_corp_id", None) or "",
        }
        return ChannelConfig(
            enabled=bool(data["token"] and data["aes_key"]),
            data=data,
        )

    def _verify_signature(
        self, *, token: str, ts: str, nonce: str, echostr_or_body: str,
        received_sig: str,
    ) -> None:
        expected = sha1_hex(token, ts, nonce, echostr_or_body)
        if not constant_time_equals(expected, received_sig):
            raise ChannelVerificationError("wecom signature mismatch")

    def challenge_response(self, *, body, query, cfg):
        """URL verification: GET callback with ``echostr`` that must be
        decrypted and returned in plaintext. Callers hit this when
        the HTTP method is GET (the router handles method dispatch).
        """
        echostr = query.get("echostr")
        if not echostr:
            return None
        token = cfg.require("token")
        aes_key = cfg.require("aes_key")
        self._verify_signature(
            token=token,
            ts=query.get("timestamp") or "",
            nonce=query.get("nonce") or "",
            echostr_or_body=echostr,
            received_sig=query.get("msg_signature") or "",
        )
        plain = _decrypt_aes_cbc(aes_key, echostr)
        return {"_plaintext_echo": plain.decode("utf-8", errors="replace")}

    def verify_and_extract(self, *, headers, body, query, cfg):
        token = cfg.require("token")
        aes_key = cfg.require("aes_key")

        envelope = _parse_xml_or_json(body)
        encrypt_b64 = (
            envelope.get("Encrypt") or envelope.get("encrypt") or ""
        )
        if not encrypt_b64:
            raise ChannelVerificationError("wecom body missing Encrypt field")

        self._verify_signature(
            token=token,
            ts=query.get("timestamp") or "",
            nonce=query.get("nonce") or "",
            echostr_or_body=encrypt_b64,
            received_sig=query.get("msg_signature") or "",
        )

        plain = _decrypt_aes_cbc(aes_key, encrypt_b64)
        # Plaintext is XML — convert to dict.
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(plain.decode("utf-8"))
        except Exception as exc:
            raise ChannelVerificationError(f"wecom plaintext not XML: {exc}")
        return {child.tag: (child.text or "") for child in root}


register_receiver(WeComReceiver())


# ---------------------------------------------------------------------------
# Pending-approval notifier (Charter §1 渠道闭环)
# ---------------------------------------------------------------------------

# WeChat iLink can't render interactive buttons, so the approval prompt is
# plain text. Operators reply with ``通过 <prefix>`` / ``拒绝 <prefix>``;
# the inbound parser in :mod:`hermes_cloud.app.celery_app` short-circuits
# the agent invocation and routes those into ``dispatcher.approve/reject``.
_APPROVAL_TEXT_TEMPLATE = (
    "🛡️ 等待审批\n"
    "{kind}（由 {agent} 提交）\n"
    "{summary}\n\n"
    "回复 “通过 {prefix}” 或 “拒绝 {prefix}” 来处理。"
)


def build_gateway_approval_text(action: Dict[str, Any]) -> str:
    """Render an Action Gateway approval prompt as iLink-friendly text."""
    action_id = str(action.get("id") or "")
    # Use the first 8 chars of the UUID — short enough to type, plenty
    # collision-resistant against the few pending actions a single user
    # has open at once. The parser matches by prefix.
    prefix = action_id[:8] if action_id else ""
    return _APPROVAL_TEXT_TEMPLATE.format(
        kind=str(action.get("kind") or ""),
        agent=str(action.get("agent_name") or ""),
        summary=(str(action.get("summary") or "").strip() or "（无摘要）"),
        prefix=prefix,
    )


def _wecom_ilink_pending_notifier(
    action: Dict[str, Any], origin: Dict[str, Any],
) -> None:
    """Deliver an Action Gateway approval prompt to the wecom_ilink channel.

    Charter §1 routes pending approvals back to the channel that
    originated them. iLink delivery flows through the cloud's
    ``/internal/channel/push`` endpoint (which forwards to the
    per-profile hermes subprocess that owns the user's bot session).
    Missing wxid → log + bail; the WebUI dashboard remains the
    fallback so the action itself isn't lost.
    """
    from hermes_cloud.app.insights.notify import _post_via_cloud

    channel_id = str(origin.get("external_user_id") or "").strip()
    if not channel_id:
        logger.warning(
            "wecom_ilink pending notifier: missing external_user_id "
            "(action=%s) — falling back to webui dashboard",
            action.get("id"),
        )
        return

    profile_id = str(action.get("profile_id") or "").strip()
    if not profile_id:
        logger.warning(
            "wecom_ilink pending notifier: missing profile_id (action=%s)",
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
        platform="weixin",
        channel_id=channel_id,
        text=text,
    )
    if not ok:
        logger.warning(
            "wecom_ilink pending notifier: text POST failed action=%s wxid=%s",
            action.get("id"), channel_id,
        )


def _register_pending_notifier() -> None:
    try:
        from hermes.action_gateway import notifiers
        notifiers.register("wecom_ilink", _wecom_ilink_pending_notifier)
    except Exception:
        logger.exception("wecom_ilink: pending_notifier registration failed")


_register_pending_notifier()


__all__ = ["WeComReceiver", "build_gateway_approval_text"]
