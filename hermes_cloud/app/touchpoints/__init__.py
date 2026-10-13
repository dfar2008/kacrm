"""Touchpoints — inbound adapters feeding the Interaction Log.

Every external channel that produces a customer interaction becomes
a thin adapter in this package. Each adapter normalizes its native
payload shape and calls :func:`hermes_cloud.app.interactions.emitter.emit`
with a canonical ``(kind, actors, subjects, content, source)`` tuple.

Downstream (Memory Substrate, Agent Pool, Projections) does not know
or care which adapter produced a row — the charter's original promise
(反转 1: "客户 = 所有邮件、通话转写、会议纪要、消息、文档构成的
时序事件流") is kept only if every touchpoint lands in the same table.

Adapter contract:

    class XxxAdapter(TouchpointAdapter):
        source = "channel:xxx"
        default_kind = "im_message"

        def normalize(self, payload) -> NormalizedTouchpoint:
            ...

Adapters are registered at import time via :func:`register_adapter`;
:func:`get_adapter(source)` resolves the string a webhook URL carried
back to the implementation class.
"""
from hermes_cloud.app.touchpoints.base import (  # noqa: F401
    NormalizedTouchpoint,
    TouchpointAdapter,
    get_adapter,
    list_adapters,
    register_adapter,
    ingest,
)

# Side-effect imports: each module calls register_adapter() at load.
from hermes_cloud.app.touchpoints import (  # noqa: F401
    manual,
    feishu,
    wecom,
    wechat,
    dingtalk,
    slack,
    telegram,
    discord,
    email as email_adapter,
    webhook as webhook_adapter,
    voice_memo,
    call,
    reclassify,
)
