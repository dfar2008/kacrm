"""Slack Events API receiver.

Verification per https://api.slack.com/authentication/verifying-requests-from-slack:

    sig_basestring = "v0:" + ts + ":" + body
    expected       = "v0=" + HMAC_SHA256(signing_secret, sig_basestring)
    compare expected with X-Slack-Signature (constant-time)

Reject if ts is older than 5 minutes (replay).

URL verification: Slack POSTs ``{"type":"url_verification","challenge":"..."}``
on app-level events; reply with the challenge echo (JSON).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from hermes_cloud.app.channels.base import (
    ChannelConfig, ChannelReceiver, ChannelVerificationError,
    constant_time_equals, hmac_sha256_hex, register_receiver,
)


_REPLAY_WINDOW_SECONDS = 60 * 5


class SlackReceiver(ChannelReceiver):
    source = "channel:slack"
    touchpoint_source = "channel:slack"

    def load_config(self, cfg: Any) -> ChannelConfig:
        secret = getattr(cfg, "slack_signing_secret", None)
        enabled = bool(secret)
        return ChannelConfig(
            enabled=enabled, data={"signing_secret": secret or ""},
        )

    def challenge_response(self, *, body, query, cfg):
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        if isinstance(data, dict) and data.get("type") == "url_verification":
            return {"challenge": data.get("challenge", "")}
        return None

    def verify_and_extract(self, *, headers, body, query, cfg):
        # URL verification short-circuits before HMAC (Slack spec).
        ch = self.challenge_response(body=body, query=query, cfg=cfg)
        if ch is not None:
            return None

        ts = headers.get("x-slack-request-timestamp") or ""
        sig = headers.get("x-slack-signature") or ""
        if not ts or not sig:
            raise ChannelVerificationError("missing slack signature headers")
        try:
            if abs(time.time() - int(ts)) > _REPLAY_WINDOW_SECONDS:
                raise ChannelVerificationError("slack timestamp outside replay window")
        except ValueError:
            raise ChannelVerificationError("slack timestamp not an integer")

        secret = cfg.require("signing_secret")
        basestring = f"v0:{ts}:{body.decode('utf-8')}"
        expected = "v0=" + hmac_sha256_hex(secret, basestring)
        if not constant_time_equals(expected, sig):
            raise ChannelVerificationError("slack signature mismatch")

        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ChannelVerificationError(f"slack body not valid JSON: {exc}")


register_receiver(SlackReceiver())


# Charter §1 渠道闭环: text-based approval prompt — Slack Block Kit
# could deliver clickable buttons; future enhancement. Reply-parser
# fast-path handles 通过/拒绝 commands uniformly.
def _register_pending_notifier() -> None:
    try:
        from hermes.action_gateway import notifiers, text_notifier
        notifiers.register(
            "slack",
            text_notifier.make_notifier(channel="slack", platform="slack"),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "slack: pending_notifier registration failed",
        )


_register_pending_notifier()


__all__ = ["SlackReceiver"]
