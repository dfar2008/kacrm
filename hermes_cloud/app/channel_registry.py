"""Channel specifications — per-channel field schema, secret-field
masking, validation, and test-connection probes.

Each entry in :data:`CHANNEL_REGISTRY` defines how the cloud handles
one messaging channel's credentials: required form fields, which
fields are secrets (masked when returned via GET), a validator that
raises on bad shape, and an async ``test_fn`` that probes whether
the stored creds actually work.

Adding a new channel = add one ChannelSpec entry. The generic
REST handlers in main.py iterate this registry — no per-channel
endpoint code.

See ``docs/superpowers/plans/2026-05-19-channel-per-user-bot.md``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """Outcome of one test-connection probe.

    ``ok`` is the only field UIs need to flip the status pill; the
    other fields surface diagnostics in the inline error message and
    feed observability dashboards.
    """

    ok: bool
    error: str = ""
    latency_ms: int = 0
    bot_name: str = ""  # populated by some probes (Feishu, Telegram getMe)


# Async probe signature: ``(extra, profile_dir) -> ProbeResult``. Most
# probes only need ``extra``; weixin also needs ``profile_dir`` to load
# its iLink token from the profile's per-account file.
ProbeFn = Callable[[Dict[str, Any], Path], Awaitable[ProbeResult]]


class ChannelConfigError(ValueError):
    """Raised by a channel's validator when the submitted extra dict is
    missing required fields or has the wrong shape. Surfaced as HTTP
    400 by the REST handler."""


@dataclass
class ChannelSpec:
    """Per-channel contract: what fields, what's secret, how to probe."""

    name: str
    display_name: str                 # SPA-facing label, e.g. "飞书 Feishu"
    required_fields: List[str]
    secret_fields: List[str]          # masked when returned via GET
    optional_fields: List[str] = field(default_factory=list)
    supports_qr: bool = False         # Feishu, WeChat have native QR flows
    validator: Optional[Callable[[Dict[str, Any]], None]] = None
    test_fn: Optional[ProbeFn] = None

    def known_fields(self) -> List[str]:
        return self.required_fields + self.optional_fields

    def mask_secrets(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of ``extra`` with secret fields masked.

        Uses a fixed ●●●● prefix + last-4 so the SPA can show "is
        configured" without the user having to type the full secret
        again. Empty/short secrets are masked to a generic placeholder
        rather than revealing length.
        """
        out: Dict[str, Any] = {}
        for k, v in extra.items():
            if k in self.secret_fields and isinstance(v, str) and v:
                if len(v) <= 4:
                    out[k] = "●●●●"
                else:
                    out[k] = f"●●●●●●{v[-4:]}"
            else:
                out[k] = v
        return out

    def validate(self, extra: Dict[str, Any]) -> None:
        """Check required fields + delegate to per-channel validator.

        Required fields must be present and non-empty strings.
        Anything beyond ``known_fields()`` is allowed (lets hermes
        accept platform-specific extras the cloud doesn't know about
        yet — forward-compat).
        """
        if not isinstance(extra, dict):
            raise ChannelConfigError("extra must be a JSON object")
        for f in self.required_fields:
            val = extra.get(f)
            if not isinstance(val, str) or not val.strip():
                raise ChannelConfigError(
                    f"channel={self.name}: field '{f}' is required (non-empty string)",
                )
        if self.validator is not None:
            self.validator(extra)


# ---------------------------------------------------------------------------
# Per-channel validators
# ---------------------------------------------------------------------------


def _validate_feishu(extra: Dict[str, Any]) -> None:
    domain = (extra.get("domain") or "feishu").strip().lower()
    if domain not in ("feishu", "lark"):
        raise ChannelConfigError(
            "channel=feishu: 'domain' must be 'feishu' or 'lark'",
        )
    conn = (extra.get("connection_mode") or "websocket").strip().lower()
    if conn not in ("websocket", "webhook"):
        raise ChannelConfigError(
            "channel=feishu: 'connection_mode' must be 'websocket' or 'webhook'",
        )


def _validate_weixin(extra: Dict[str, Any]) -> None:
    # account_id format: lowercase hex + '@im.bot' suffix (iLink convention)
    account = extra.get("account_id", "")
    if "@" not in account:
        raise ChannelConfigError(
            "channel=weixin: 'account_id' must look like 'xxx@im.bot' "
            "(get it from QR login)",
        )


def _validate_telegram(extra: Dict[str, Any]) -> None:
    # Telegram bot token format: <bot_id>:<auth_token>, total ~46 chars
    token = extra.get("token", "")
    if ":" not in token:
        raise ChannelConfigError(
            "channel=telegram: 'token' must be in '<bot_id>:<secret>' format "
            "(get it from @BotFather)",
        )


def _validate_wecom(extra: Dict[str, Any]) -> None:
    # WeCom AI Bot stream creds = bot_id + secret (enforced by
    # required_fields). No further shape constraint; the live WebSocket
    # auth is the real validity check.
    pass


def _validate_dingtalk(extra: Dict[str, Any]) -> None:
    # No strict format constraint beyond required fields; Dingtalk has
    # several app types (mini-app, H5, robot) with different field
    # combinations. Accept anything the user pastes and let test_fn
    # catch wrong shapes via the actual API.
    pass


def _validate_discord(extra: Dict[str, Any]) -> None:
    token = extra.get("token", "")
    # Discord bot tokens are typically 59-72 chars; cheap sanity check
    if len(token) < 20:
        raise ChannelConfigError(
            "channel=discord: 'token' looks too short for a Discord bot token "
            "(get it from https://discord.com/developers/applications)",
        )


def _validate_slack(extra: Dict[str, Any]) -> None:
    token = extra.get("token", "")
    if not token.startswith(("xoxb-", "xoxp-", "xoxa-")):
        raise ChannelConfigError(
            "channel=slack: 'token' must start with xoxb-/xoxp-/xoxa- "
            "(get it from your Slack app's OAuth & Permissions page)",
        )


# ---------------------------------------------------------------------------
# Probes — a channel with ``test_fn=None`` has no probe: the SPA hides
# its "test connection" button (the spec endpoints report
# ``supports_probe=False``) and the /test endpoint returns 501. A
# channel gets a real probe when its creds can be cheaply verified —
# feishu/telegram hit a public API with creds straight from ``extra``;
# weixin loads its iLink token from the profile and calls ``getconfig``.
# ---------------------------------------------------------------------------


# Feishu / Lark API domains for the tenant_access_token endpoint.
_FEISHU_OPEN_DOMAIN = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}


async def _probe_feishu(extra: Dict[str, Any], profile_dir: Path) -> ProbeResult:
    """Verify Feishu creds by fetching a tenant_access_token.

    A successful token fetch (code == 0) proves the app_id + app_secret
    pair is valid and the app is enabled. Cheap, no side effects — the
    same call the SDK makes on every send.
    """
    import time as _time
    import httpx

    app_id = extra.get("app_id", "")
    app_secret = extra.get("app_secret", "")
    domain = (extra.get("domain") or "feishu").strip().lower()
    base = _FEISHU_OPEN_DOMAIN.get(domain, _FEISHU_OPEN_DOMAIN["feishu"])
    url = f"{base}/open-apis/auth/v3/tenant_access_token/internal"
    started = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url, json={"app_id": app_id, "app_secret": app_secret},
            )
        payload = resp.json()
    except Exception as exc:
        return ProbeResult(ok=False, error=f"network error: {exc}")
    latency = int((_time.monotonic() - started) * 1000)
    code = payload.get("code")
    if code == 0:
        return ProbeResult(ok=True, latency_ms=latency)
    return ProbeResult(
        ok=False,
        error=f"Feishu rejected the credentials (code={code}: "
              f"{payload.get('msg', 'unknown')})",
        latency_ms=latency,
    )


async def _probe_telegram(extra: Dict[str, Any], profile_dir: Path) -> ProbeResult:
    """Verify a Telegram bot token via the getMe endpoint."""
    import time as _time
    import httpx

    token = extra.get("token", "")
    url = f"https://api.telegram.org/bot{token}/getMe"
    started = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
        payload = resp.json()
    except Exception as exc:
        return ProbeResult(ok=False, error=f"network error: {exc}")
    latency = int((_time.monotonic() - started) * 1000)
    if payload.get("ok"):
        bot = payload.get("result") or {}
        return ProbeResult(
            ok=True, latency_ms=latency,
            bot_name=bot.get("username") or bot.get("first_name") or "",
        )
    return ProbeResult(
        ok=False,
        error=f"Telegram rejected the token: {payload.get('description', 'unknown')}",
        latency_ms=latency,
    )


async def _probe_weixin(extra: Dict[str, Any], profile_dir: Path) -> ProbeResult:
    """Verify Weixin (iLink) creds by calling the bot ``getconfig`` API.

    The iLink ``token`` lives in config.yaml ``platforms.weixin.extra``
    (written by the QR-login flow), the same source the runtime adapter
    reads. We probe with hermes' ``_get_config`` so the cloud never
    reimplements the iLink protocol (request signing, the non-public-CA
    TLS handling).

    ``getconfig`` is a cheap read (typing-ticket fetch) with no message
    cursor side effects, so it won't disturb the running adapter.

    iLink is not publicly documented; ``ret==0`` reliably means the
    token is valid. The ``-14`` (session expired) / ``-2`` (rate
    limited) mappings are best-effort — any other non-zero ``ret`` is
    surfaced verbatim for the user to read.
    """
    import time as _time

    try:
        import aiohttp
        from gateway.platforms.weixin import (  # type: ignore[import-not-found]
            ILINK_BASE_URL,
            _get_config,
            _make_ssl_connector,
        )
    except Exception as exc:  # pragma: no cover - import guard
        return ProbeResult(ok=False, error=f"weixin probe unavailable: {exc}")

    account_id = (extra.get("account_id") or "").strip()
    if not account_id:
        return ProbeResult(ok=False, error="account_id 缺失，请先扫码登录微信")

    token = (extra.get("token") or "").strip()
    if not token:
        return ProbeResult(
            ok=False,
            error="找不到微信账号凭证（token），请重新扫码登录",
        )
    base_url = (extra.get("base_url") or ILINK_BASE_URL).strip()
    user_id = (extra.get("user_id") or account_id).strip()

    started = _time.monotonic()
    try:
        async with aiohttp.ClientSession(
            trust_env=True, connector=_make_ssl_connector(),
        ) as session:
            resp = await _get_config(
                session,
                base_url=base_url,
                token=token,
                user_id=user_id,
                context_token=None,
            )
    except Exception as exc:
        return ProbeResult(ok=False, error=f"network error: {exc}")
    latency = int((_time.monotonic() - started) * 1000)

    ret = resp.get("ret")
    errcode = resp.get("errcode")
    # Only a genuine session/auth failure means "re-bind": -14 = session
    # expired, -2 = rate limited (transient). Any OTHER getconfig response
    # means the token authenticated and the bot is reachable — including
    # ret=-4 "GetTypingTicket rpc failed", which a cold probe (no
    # conversation context_token) legitimately triggers. The typing ticket
    # is a per-conversation cosmetic sub-RPC the adapter fetches
    # best-effort; its failure does NOT mean the connection is down (live
    # cron delivery to the same account succeeds). Treat it as connected.
    if ret == -14 or errcode == -14:
        return ProbeResult(
            ok=False, latency_ms=latency,
            error="微信会话已过期，请重新扫码登录",
        )
    if ret == -2 or errcode == -2:
        return ProbeResult(
            ok=False, latency_ms=latency,
            error="iLink 接口被限频，请稍后再试",
        )
    return ProbeResult(ok=True, latency_ms=latency)


async def _probe_email(extra: Dict[str, Any], profile_dir: Path) -> ProbeResult:
    """Verify email creds by logging into IMAP and (if configured) SMTP.

    Both legs run in a thread executor — ``imaplib`` / ``smtplib`` are
    blocking. Failures surface the server's raw rejection text (Tencent
    Exmail / Gmail / 163 all return distinct, useful strings) so the
    user gets an actionable error instead of "couldn't connect".

    SMTP leg is skipped when ``smtp_host`` is empty — receive-only
    setups are valid. Port handling: 465 → implicit TLS via
    ``SMTP_SSL``; anything else → ``SMTP`` + ``STARTTLS`` (587 is the
    submission-port norm).
    """
    import asyncio as _asyncio
    import imaplib
    import smtplib
    import ssl as _ssl
    import time as _time

    address = (extra.get("address") or "").strip()
    password = extra.get("password") or ""
    imap_host = (extra.get("imap_host") or "").strip()
    smtp_host = (extra.get("smtp_host") or "").strip()
    if not (address and password and imap_host):
        return ProbeResult(
            ok=False,
            error="missing required field(s): address / password / imap_host",
        )

    try:
        imap_port = int(str(extra.get("imap_port") or "993"))
    except ValueError:
        return ProbeResult(ok=False, error=f"invalid imap_port={extra.get('imap_port')!r}")
    try:
        smtp_port = int(str(extra.get("smtp_port") or "587"))
    except ValueError:
        return ProbeResult(ok=False, error=f"invalid smtp_port={extra.get('smtp_port')!r}")

    def _decode_imap_err(exc: BaseException) -> str:
        # imaplib raises imaplib.IMAP4.error with bytes args from the
        # server — decode so the message renders in the SPA toast.
        parts: List[str] = []
        for a in getattr(exc, "args", ()) or ():
            if isinstance(a, (bytes, bytearray)):
                parts.append(a.decode("utf-8", "replace"))
            else:
                parts.append(str(a))
        return f"{type(exc).__name__}: {' '.join(parts).strip() or exc!s}"

    def _imap_check() -> str:
        try:
            c = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=10)
        except Exception as exc:
            return f"connect failed: {type(exc).__name__}: {exc}"
        try:
            c.login(address, password)
        except imaplib.IMAP4.error as exc:
            return _decode_imap_err(exc)
        except Exception as exc:
            return f"login failed: {type(exc).__name__}: {exc}"
        finally:
            try:
                c.logout()
            except Exception:
                pass
        return ""

    def _smtp_check() -> str:
        ctx = _ssl.create_default_context()
        try:
            if smtp_port == 465:
                s = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10, context=ctx)
            else:
                s = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
        except Exception as exc:
            return f"connect failed: {type(exc).__name__}: {exc}"
        try:
            s.login(address, password)
        except smtplib.SMTPAuthenticationError as exc:
            code = exc.smtp_code
            msg = exc.smtp_error
            if isinstance(msg, (bytes, bytearray)):
                msg = msg.decode("utf-8", "replace")
            return f"auth failed ({code}): {msg}"
        except Exception as exc:
            return f"login failed: {type(exc).__name__}: {exc}"
        finally:
            try:
                s.quit()
            except Exception:
                pass
        return ""

    loop = _asyncio.get_running_loop()
    started = _time.monotonic()
    imap_err = await loop.run_in_executor(None, _imap_check)
    smtp_err = ""
    if smtp_host:
        smtp_err = await loop.run_in_executor(None, _smtp_check)
    latency = int((_time.monotonic() - started) * 1000)

    if not imap_err and not smtp_err:
        return ProbeResult(ok=True, latency_ms=latency, bot_name=address)

    parts: List[str] = []
    if imap_err:
        parts.append(f"IMAP: {imap_err}")
    if smtp_err:
        parts.append(f"SMTP: {smtp_err}")
    return ProbeResult(ok=False, latency_ms=latency, error=" / ".join(parts))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


CHANNEL_REGISTRY: Dict[str, ChannelSpec] = {
    "feishu": ChannelSpec(
        name="feishu",
        display_name="飞书 Feishu",
        required_fields=["app_id", "app_secret"],
        secret_fields=["app_secret", "encrypt_key"],
        optional_fields=[
            "encrypt_key", "verification_token", "domain",
            "connection_mode", "bot_name", "bot_open_id",
        ],
        supports_qr=True,
        validator=_validate_feishu,
        test_fn=_probe_feishu,
    ),
    "weixin": ChannelSpec(
        name="weixin",
        display_name="微信 WeChat",
        required_fields=["account_id", "token"],
        secret_fields=["token"],
        optional_fields=["base_url", "cdn_base_url", "user_id"],
        supports_qr=True,
        validator=_validate_weixin,
        test_fn=_probe_weixin,
    ),
    "wecom": ChannelSpec(
        name="wecom",
        display_name="企业微信 WeCom",
        # AI Bot Stream (WebSocket). QR scan 回 bot_id+secret.
        required_fields=["bot_id", "secret"],
        secret_fields=["secret"],
        optional_fields=[],
        supports_qr=True,
        validator=_validate_wecom,
    ),
    "telegram": ChannelSpec(
        name="telegram",
        display_name="Telegram",
        required_fields=["token"],
        secret_fields=["token"],
        supports_qr=False,
        validator=_validate_telegram,
        test_fn=_probe_telegram,
    ),
    "discord": ChannelSpec(
        name="discord",
        display_name="Discord",
        required_fields=["token"],
        secret_fields=["token"],
        optional_fields=["app_id", "guild_id"],
        supports_qr=False,
        validator=_validate_discord,
    ),
    "slack": ChannelSpec(
        name="slack",
        display_name="Slack",
        required_fields=["token"],
        secret_fields=["token", "signing_secret", "app_token"],
        optional_fields=["signing_secret", "app_token"],
        supports_qr=False,
        validator=_validate_slack,
    ),
    "dingtalk": ChannelSpec(
        name="dingtalk",
        display_name="钉钉 Dingtalk",
        # Stream Mode (扫码/手填都落这组). QR device-flow 回 client_id+client_secret.
        required_fields=["client_id", "client_secret"],
        # robot_code 虽是标识符,沿用旧 spec 脱敏行为(避免 GET 暴露回退).
        secret_fields=["client_secret", "robot_code"],
        optional_fields=["robot_code"],
        supports_qr=True,
        validator=_validate_dingtalk,
    ),
    # Email is stored in the profile's .env (EMAIL_* env vars), not
    # config.yaml — hermes' EmailAdapter reads env vars. The cloud's
    # channel read/write layer special-cases "email" accordingly.
    "email": ChannelSpec(
        name="email",
        display_name="邮件 Email",
        required_fields=["address", "password", "imap_host", "smtp_host"],
        secret_fields=["password"],
        optional_fields=[
            "imap_port", "smtp_port", "poll_interval",
            "allowed_users", "home_address",
            "owner_users", "persona", "signature", "customer_reply_guide",
        ],
        supports_qr=False,
        test_fn=_probe_email,
    ),
}


def get_spec(channel: str) -> ChannelSpec:
    """Return the spec for a channel name, raising ``KeyError`` if
    unknown. Callers should catch and turn this into a 400 response."""
    return CHANNEL_REGISTRY[channel]


def list_channels() -> List[str]:
    """Return all registered channel names in stable order — useful
    for the SPA's "Channels" list and dashboards."""
    return list(CHANNEL_REGISTRY.keys())
