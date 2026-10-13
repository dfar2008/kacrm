"""Discord Interactions / webhook receiver.

Discord signs every request with Ed25519 over ``ts + body`` using the
application's public key. Verification requires PyNaCl; if it's not
installed we fail-closed (503-ish via ChannelVerificationError).

Discord's URL-verification analogue: the very first request after
registering the endpoint is a PING (``type=1``), to which you reply
``{"type":1}`` (PONG). We handle that inside
:meth:`challenge_response`.
"""
from __future__ import annotations

import json
from typing import Any

from hermes_cloud.app.channels.base import (
    ChannelConfig, ChannelReceiver, ChannelVerificationError,
    register_receiver,
)


def _verify_ed25519(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
    try:
        from nacl.signing import VerifyKey          # type: ignore[import-not-found]
        from nacl.exceptions import BadSignatureError  # type: ignore[import-not-found]
    except Exception as exc:  # PyNaCl missing
        raise ChannelVerificationError(
            f"discord: PyNaCl is required for Ed25519 verification ({exc})"
        )
    try:
        vk = VerifyKey(bytes.fromhex(public_key_hex))
        vk.verify(message, bytes.fromhex(signature_hex))
        return True
    except BadSignatureError:
        return False
    except Exception as exc:
        raise ChannelVerificationError(f"discord verify failed: {exc}")


class DiscordReceiver(ChannelReceiver):
    source = "channel:discord"
    touchpoint_source = "channel:discord"

    def load_config(self, cfg: Any) -> ChannelConfig:
        pk = getattr(cfg, "discord_public_key", None)
        return ChannelConfig(enabled=bool(pk), data={"public_key": pk or ""})

    def challenge_response(self, *, body, query, cfg):
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        if isinstance(data, dict) and data.get("type") == 1:  # PING
            return {"type": 1}  # PONG
        return None

    def verify_and_extract(self, *, headers, body, query, cfg):
        sig = headers.get("x-signature-ed25519") or ""
        ts = headers.get("x-signature-timestamp") or ""
        if not sig or not ts:
            raise ChannelVerificationError("missing discord signature headers")

        pk = cfg.require("public_key")
        message = ts.encode("utf-8") + body
        if not _verify_ed25519(pk, message, sig):
            raise ChannelVerificationError("discord ed25519 mismatch")

        # After verification, PING → PONG short-circuit.
        ch = self.challenge_response(body=body, query=query, cfg=cfg)
        if ch is not None:
            return None

        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ChannelVerificationError(f"discord body not valid JSON: {exc}")


register_receiver(DiscordReceiver())


# Charter §1 渠道闭环: text-based approval prompt — Discord components
# could deliver clickable buttons; that's a future enhancement. The
# reply-command parser handles "通过 <prefix>" / "拒绝 <prefix>" via the
# celery intake fast-path uniformly across channels.
def _register_pending_notifier() -> None:
    try:
        from hermes.action_gateway import notifiers, text_notifier
        notifiers.register(
            "discord",
            text_notifier.make_notifier(channel="discord", platform="discord"),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "discord: pending_notifier registration failed",
        )


_register_pending_notifier()


__all__ = ["DiscordReceiver"]
