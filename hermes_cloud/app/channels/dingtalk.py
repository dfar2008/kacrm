"""DingTalk (钉钉) receiver.

Two common setups:

1. **Stream-mode / outgoing robot HMAC**: DingTalk sends
   ``timestamp`` + ``sign`` headers; sign = base64(HMAC-SHA256(
   app_secret, timestamp + "\\n" + app_secret)). We verify that.
2. **Event subscription w/ AES encrypt**: DingTalk sends a ``signature``
   query + encrypted body; needs aes_key decryption. Supported here
   when ``encoding_aes_key`` + ``token`` + ``app_key`` are provided.

Which path fires depends on which keys are configured.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from hermes_cloud.app.channels.base import (
    ChannelConfig, ChannelReceiver, ChannelVerificationError,
    constant_time_equals, hmac_sha256_b64, sha1_hex,
    register_receiver,
)


def _decrypt_aes_cbc(encoding_aes_key: str, encrypted_b64: str) -> bytes:
    """WeCom/DingTalk/Feishu all share this shape — AES-256-CBC with
    a 43-char base64-ish ``EncodingAESKey`` that decodes to 32 bytes.
    """
    try:
        from Crypto.Cipher import AES  # type: ignore[import-not-found]
    except Exception as exc:
        raise ChannelVerificationError(
            f"AES decrypt needs pycryptodome: {exc}"
        )
    key = base64.b64decode(encoding_aes_key + "=")
    if len(key) != 32:
        raise ChannelVerificationError("encoding_aes_key must decode to 32 bytes")
    iv = key[:16]
    ct = base64.b64decode(encrypted_b64)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plain = cipher.decrypt(ct)
    # PKCS7 de-pad
    pad = plain[-1]
    if isinstance(pad, str):  # py2-compat safety
        pad = ord(pad)
    plain = plain[:-pad]
    # DingTalk/WeCom framing:
    #   16 random bytes | 4 bytes msg_len (big-endian) | msg | corp_or_app_id
    if len(plain) < 20:
        raise ChannelVerificationError("decrypted payload too short")
    msg_len = int.from_bytes(plain[16:20], "big")
    msg = plain[20:20 + msg_len]
    return msg


class DingTalkReceiver(ChannelReceiver):
    source = "channel:dingtalk"
    touchpoint_source = "channel:dingtalk"

    def load_config(self, cfg: Any) -> ChannelConfig:
        data = {
            "app_secret": getattr(cfg, "dingtalk_app_secret", None) or "",
            "token": getattr(cfg, "dingtalk_token", None) or "",
            "aes_key": getattr(cfg, "dingtalk_encoding_aes_key", None) or "",
            "app_key": getattr(cfg, "dingtalk_app_key", None) or "",
        }
        return ChannelConfig(
            enabled=bool(data["app_secret"] or data["aes_key"]),
            data=data,
        )

    def verify_and_extract(self, *, headers, body, query, cfg):
        # Path A — outgoing-robot HMAC (headers present)
        ts = headers.get("timestamp") or ""
        sig = headers.get("sign") or ""
        if ts and sig:
            secret = cfg.require("app_secret")
            basestring = f"{ts}\n{secret}"
            expected = hmac_sha256_b64(secret, basestring)
            if not constant_time_equals(expected, sig):
                raise ChannelVerificationError("dingtalk HMAC mismatch")
            try:
                return json.loads(body.decode("utf-8"))
            except Exception as exc:
                raise ChannelVerificationError(f"dingtalk body not JSON: {exc}")

        # Path B — event subscription with AES + token signature
        token = cfg.get("token")
        aes_key = cfg.get("aes_key")
        if token and aes_key:
            q_sig = query.get("signature") or ""
            q_ts = query.get("timestamp") or ""
            q_nonce = query.get("nonce") or ""
            try:
                envelope = json.loads(body.decode("utf-8"))
            except Exception as exc:
                raise ChannelVerificationError(f"dingtalk envelope not JSON: {exc}")
            encrypt_b64 = envelope.get("encrypt") or ""
            expected_sig = sha1_hex(token, q_ts, q_nonce, encrypt_b64)
            if not constant_time_equals(expected_sig, q_sig):
                raise ChannelVerificationError("dingtalk event signature mismatch")
            plain = _decrypt_aes_cbc(aes_key, encrypt_b64)
            try:
                return json.loads(plain.decode("utf-8"))
            except Exception as exc:
                raise ChannelVerificationError(f"dingtalk decrypted not JSON: {exc}")

        raise ChannelVerificationError(
            "dingtalk: neither HMAC nor AES config present to verify"
        )


register_receiver(DingTalkReceiver())


# Charter §1 渠道闭环: text-based approval prompt — DingTalk action
# cards could deliver clickable buttons; future enhancement.
def _register_pending_notifier() -> None:
    try:
        from hermes.action_gateway import notifiers, text_notifier
        notifiers.register(
            "dingtalk",
            text_notifier.make_notifier(channel="dingtalk", platform="dingtalk"),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "dingtalk: pending_notifier registration failed",
        )


_register_pending_notifier()


__all__ = ["DingTalkReceiver"]
