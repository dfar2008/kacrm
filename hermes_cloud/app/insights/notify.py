"""Channel-push helpers — deliver agent output to a user's bound bot.

``_post_via_cloud`` is the shared delivery primitive — used by the
per-channel Action Gateway pending-approval notifiers (``channels/wecom``,
``channels/feishu``, …) to deliver an approval prompt back to the user.

It looks up the user's per-profile hermes subprocess
via the cloud's unified ``POST /internal/channel/push`` endpoint, so
any platform with a working ``adapter.send`` gets delivery for free.

Failures are logged and swallowed — a downed bot can't break the
caller.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)


# Where the cloud's FastAPI listens internally. Celery workers run in
# a separate process from the API so they reach it via HTTP on loopback.
CLOUD_INTERNAL_URL_ENV = "CLOUD_INTERNAL_URL"
_DEFAULT_CLOUD_URL = "http://127.0.0.1:8765"
# Shared secret for ``X-Internal-Key`` auth on cloud internal routes.
# Must match ``AppConfig.internal_key`` (which the FastAPI process
# loads from the same source at startup).
INTERNAL_KEY_ENV = "HERMES_CLOUD_INTERNAL_KEY"


def _post_via_cloud(*, user_id: int, profile_id: str, platform: str,
                    channel_id: str, text: str) -> bool:
    """POST to the cloud's ``/internal/channel/push`` which forwards to
    the user's hermes subprocess. Returns True on HTTP 200 + ok=True.
    """
    base = (os.environ.get(CLOUD_INTERNAL_URL_ENV) or _DEFAULT_CLOUD_URL).rstrip("/")
    # Charter v2 §一系统 — loopback-trusted; empty key fine on same machine.
    key = os.environ.get(INTERNAL_KEY_ENV) or ""
    url = f"{base}/internal/channel/push"
    try:
        resp = requests.post(
            url,
            json={
                "user_id": str(user_id),
                "profile_id": profile_id or str(user_id),
                "platform": platform,
                "channel_id": channel_id,
                "text": text,
            },
            headers={"X-Internal-Key": key},
            # Cloud /internal/channel/push forwards to a per-profile
            # hermes subprocess via app.state.http_client. iLink
            # send-message has its own retry/backoff loop (~30s) plus
            # subprocess ensure-running (~30s cold start), so 15s was
            # too tight on the cloud-warm path. 90s gives one full
            # iLink retry cycle without prematurely failing.
            timeout=90,
        )
    except Exception:
        logger.exception("coach push: http call failed url=%s", url)
        return False

    if resp.status_code != 200:
        logger.warning(
            "coach push: http %s user=%s platform=%s body=%s",
            resp.status_code, user_id, platform, resp.text[:500],
        )
        return False
    try:
        ok = bool(resp.json().get("ok", False))
    except Exception:
        ok = False
    if not ok:
        logger.warning(
            "coach push: ok=False user=%s platform=%s body=%s",
            user_id, platform, resp.text[:500],
        )
    return ok


# ---------------------------------------------------------------------------
# Decisions feature: WeChat (iLink) plain-text fallback (spec §六·b).
# ---------------------------------------------------------------------------

def build_decision_text(reply: Dict[str, Any], *, spa_url: str) -> str:
    """Format reply_messages as wechat-friendly plain text + SPA link.

    iLink does not render interactive cards, so action buttons can't
    be inline; we list top 3 conclusions and direct user to the SPA
    /app/decisions thread for actions.
    """
    msgs = reply.get("reply_messages") or []
    if not msgs:
        return f"今日暂无值得处理的决策点。\n详情：{spa_url}"
    lines: List[str] = ["本周值得你处理的事："]
    for i, m in enumerate(msgs[:3], 1):
        head = m.get("headline", "")
        reason = m.get("reasoning", "")
        lines.append(f"\n{i}. {head}")
        if reason:
            lines.append(f"   {reason[:120]}")
    lines.append(f"\n[查看完整对话与按钮] {spa_url}")
    return "\n".join(lines)
