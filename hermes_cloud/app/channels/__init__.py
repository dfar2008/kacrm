"""Channel receivers — the authenticated edge of the Touchpoint layer.

A **channel receiver** is the HTTP or long-connection endpoint a 3rd
party IM / messaging platform talks to. It handles the two things a
touchpoint adapter doesn't:

1. **Signature / encryption verification** — reject forged callbacks
   before they reach our Interaction Log. Each channel has its own
   signing scheme (WeCom AES + SHA1, Slack HMAC-SHA256, Discord Ed25519,
   DingTalk HMAC, Telegram secret-token header, ...).
2. **Webhook URL verification challenges** — WeCom / DingTalk / Feishu
   require a one-shot encrypted challenge response during URL setup.

Once the message is verified and decrypted, the receiver hands the
decoded payload to the matching
:class:`hermes_cloud.app.touchpoints.base.TouchpointAdapter` via
:func:`touchpoints.base.ingest`. The adapter doesn't know or care
that a signature check happened — that separation keeps the Log a
single write path.

Module layout
-------------

- :mod:`base`      — :class:`ChannelReceiver` abstract + crypto helpers
- :mod:`feishu`    — adapts existing ``FeishuLongConnClient`` to feed
                     touchpoints
- :mod:`wecom`     — AES-CBC decrypt + SHA1 signature (企微)
- :mod:`dingtalk`  — HMAC-SHA256 over timestamp+body (钉钉)
- :mod:`slack`     — v0 HMAC-SHA256 over ``v0:ts:body``
- :mod:`telegram`  — ``X-Telegram-Bot-Api-Secret-Token`` header match
- :mod:`discord`   — Ed25519 over ``ts + body``
- :mod:`email`     — generic inbound-email webhook (Postmark / SendGrid
                     style) with optional basic-auth or bearer
- :mod:`api`       — FastAPI router mounting webhook endpoints

Each receiver is best-effort: if signatures can't be verified (missing
secret, optional lib absent) the handler returns 503, never falls back
to "just trust the body" — the Log is the source of truth for RL
training and training on forged data is catastrophic.
"""
from hermes_cloud.app.channels.base import (  # noqa: F401
    ChannelReceiver,
    ChannelVerificationError,
    get_receiver,
    list_receivers,
    register_receiver,
)

# Side-effect imports: each module calls register_receiver() at load.
from hermes_cloud.app.channels import (  # noqa: F401
    wecom,
    dingtalk,
    slack,
    telegram,
    discord,
    email as email_channel,
    feishu,
)
