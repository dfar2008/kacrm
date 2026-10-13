"""Inbound email receiver — provider-agnostic webhook.

Postmark, SendGrid Inbound Parse, Cloudflare Email Workers, Mailgun —
they all POST a parsed email to a URL of your choice. We support two
authentication modes on top of that:

1. **Bearer token** in ``Authorization: Bearer <token>`` header
2. **Basic auth** (``Authorization: Basic base64(user:pass)``)

Either one is configured via AppConfig.email_inbound_secret; which
header style the provider uses is their choice. We accept both.

The payload shape is intentionally flexible — we lift the usual keys
(``from``/``From``, ``subject``/``Subject``, ``text``/``TextBody``,
``html``/``HtmlBody``, ...) in the touchpoint adapter, not here.

No URL-verification challenge.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from hermes_cloud.app.channels.base import (
    ChannelConfig, ChannelReceiver, ChannelVerificationError,
    constant_time_equals, register_receiver,
)


class EmailInboundReceiver(ChannelReceiver):
    source = "channel:email"
    touchpoint_source = "channel:email"

    def load_config(self, cfg: Any) -> ChannelConfig:
        secret = getattr(cfg, "email_inbound_secret", None)
        return ChannelConfig(enabled=bool(secret), data={"secret": secret or ""})

    def _check_auth(self, secret: str, header: str) -> None:
        if not header:
            raise ChannelVerificationError("missing Authorization header")
        scheme, _, token = header.partition(" ")
        scheme = scheme.strip().lower()
        if scheme == "bearer":
            if not constant_time_equals(secret, token.strip()):
                raise ChannelVerificationError("email: bearer token mismatch")
            return
        if scheme == "basic":
            try:
                decoded = base64.b64decode(token.strip()).decode("utf-8", "replace")
            except Exception:
                raise ChannelVerificationError("email: bad Basic token")
            _, _, pw = decoded.partition(":")
            if not constant_time_equals(secret, pw):
                raise ChannelVerificationError("email: basic password mismatch")
            return
        raise ChannelVerificationError(f"email: unsupported auth scheme {scheme!r}")

    def verify_and_extract(self, *, headers, body, query, cfg):
        secret = cfg.require("secret")
        self._check_auth(secret, headers.get("authorization") or "")
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ChannelVerificationError(f"email: body not JSON: {exc}")
        if not isinstance(data, dict):
            raise ChannelVerificationError("email: body must be a JSON object")
        return data


register_receiver(EmailInboundReceiver())

__all__ = ["EmailInboundReceiver"]
