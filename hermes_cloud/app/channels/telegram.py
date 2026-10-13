"""Telegram Bot API webhook receiver.

Telegram webhook security is minimal by design — you set a secret
token when calling ``setWebhook`` and Telegram echoes it back on every
callback in ``X-Telegram-Bot-Api-Secret-Token``. We match it in
constant time; any mismatch is a rejection.

No URL-verification challenge. No body encryption.
"""
from __future__ import annotations

import json
from typing import Any

from hermes_cloud.app.channels.base import (
    ChannelConfig, ChannelReceiver, ChannelVerificationError,
    constant_time_equals, register_receiver,
)


class TelegramReceiver(ChannelReceiver):
    source = "channel:telegram"
    touchpoint_source = "channel:telegram"

    def load_config(self, cfg: Any) -> ChannelConfig:
        token = getattr(cfg, "telegram_webhook_secret", None)
        return ChannelConfig(enabled=bool(token), data={"secret": token or ""})

    def verify_and_extract(self, *, headers, body, query, cfg):
        expected = cfg.require("secret")
        received = headers.get("x-telegram-bot-api-secret-token") or ""
        if not constant_time_equals(expected, received):
            raise ChannelVerificationError("telegram secret-token mismatch")
        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ChannelVerificationError(f"telegram body not valid JSON: {exc}")


register_receiver(TelegramReceiver())


# Charter §1 渠道闭环: text-based approval prompt + reply-command parser
# (see ``hermes.action_gateway.text_commands``). Telegram could render
# inline keyboard buttons; the text path keeps parity with the other
# channels until someone wires the richer UX.
def _register_pending_notifier() -> None:
    try:
        from hermes.action_gateway import notifiers, text_notifier
        notifiers.register(
            "telegram",
            text_notifier.make_notifier(channel="telegram", platform="telegram"),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "telegram: pending_notifier registration failed",
        )


_register_pending_notifier()


__all__ = ["TelegramReceiver"]
