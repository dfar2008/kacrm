"""Channel registry — spec validation, masking, and per-channel quirks."""
from __future__ import annotations

import pytest

from hermes_cloud.app.channel_registry import (
    CHANNEL_REGISTRY,
    ChannelConfigError,
    get_spec,
    list_channels,
)


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_all_expected_channels_registered():
    expected = {"feishu", "weixin", "wecom", "telegram", "discord", "slack", "dingtalk"}
    assert expected.issubset(set(CHANNEL_REGISTRY.keys()))


def test_list_channels_returns_stable_order():
    a = list_channels()
    b = list_channels()
    assert a == b


def test_get_spec_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        get_spec("not-a-real-channel")


def test_every_secret_field_is_in_known_fields():
    """Lint: every entry in secret_fields must also appear in
    required_fields or optional_fields, otherwise masking silently
    drops the field."""
    for spec in CHANNEL_REGISTRY.values():
        for secret in spec.secret_fields:
            assert secret in spec.known_fields(), (
                f"channel={spec.name}: secret field '{secret}' not in "
                f"known_fields {spec.known_fields()}"
            )


def test_qr_supporting_channels_are_only_feishu_weixin_wecom_dingtalk():
    """Channels with native QR / device-flow support. Updated when
    wecom and dingtalk switched to stream-mode credentials."""
    qr_channels = {n for n, s in CHANNEL_REGISTRY.items() if s.supports_qr}
    assert qr_channels == {"feishu", "weixin", "wecom", "dingtalk"}


def test_channels_with_a_real_probe():
    """Only feishu/telegram/weixin have a test-connection probe. The
    rest have test_fn=None — the spec endpoints report
    supports_probe=False and the SPA hides their "test connection"
    button. Documenting intent so accidental flips fail review."""
    probed = {n for n, s in CHANNEL_REGISTRY.items() if s.test_fn is not None}
    assert probed == {"feishu", "telegram", "weixin", "email"}


# ---------------------------------------------------------------------------
# Validation — required fields
# ---------------------------------------------------------------------------


def test_validate_feishu_rejects_missing_app_id():
    spec = get_spec("feishu")
    with pytest.raises(ChannelConfigError, match="app_id"):
        spec.validate({"app_secret": "s"})


def test_validate_feishu_rejects_empty_string_app_secret():
    spec = get_spec("feishu")
    with pytest.raises(ChannelConfigError, match="app_secret"):
        spec.validate({"app_id": "x", "app_secret": ""})


def test_validate_feishu_rejects_whitespace_only():
    spec = get_spec("feishu")
    with pytest.raises(ChannelConfigError):
        spec.validate({"app_id": "x", "app_secret": "   "})


def test_validate_feishu_rejects_non_dict():
    spec = get_spec("feishu")
    with pytest.raises(ChannelConfigError, match="JSON object"):
        spec.validate("not a dict")  # type: ignore[arg-type]


def test_validate_feishu_accepts_minimal_valid():
    spec = get_spec("feishu")
    # No exception
    spec.validate({"app_id": "cli_x", "app_secret": "s"})


def test_validate_feishu_accepts_full_valid():
    spec = get_spec("feishu")
    spec.validate({
        "app_id": "cli_x", "app_secret": "s",
        "encrypt_key": "ek", "verification_token": "vt",
        "domain": "lark", "connection_mode": "websocket",
    })


def test_validate_feishu_rejects_unknown_domain():
    spec = get_spec("feishu")
    with pytest.raises(ChannelConfigError, match="domain"):
        spec.validate({"app_id": "x", "app_secret": "s", "domain": "wechat"})


def test_validate_feishu_rejects_unknown_connection_mode():
    spec = get_spec("feishu")
    with pytest.raises(ChannelConfigError, match="connection_mode"):
        spec.validate({"app_id": "x", "app_secret": "s", "connection_mode": "smoke-signals"})


# ---------------------------------------------------------------------------
# Validation — per-channel quirks
# ---------------------------------------------------------------------------


def test_validate_weixin_rejects_account_without_at_im_bot():
    spec = get_spec("weixin")
    with pytest.raises(ChannelConfigError, match="account_id"):
        spec.validate({"account_id": "just-text", "token": "x"})


def test_validate_weixin_accepts_ilink_account():
    spec = get_spec("weixin")
    spec.validate({"account_id": "abc123@im.bot", "token": "x"})


def test_validate_telegram_rejects_token_without_colon():
    spec = get_spec("telegram")
    with pytest.raises(ChannelConfigError, match="token"):
        spec.validate({"token": "no-colon-here"})


def test_validate_telegram_accepts_botfather_token():
    spec = get_spec("telegram")
    spec.validate({"token": "1234567890:ABCDEFghijklmnopQRSTuvwxyz"})


def test_validate_wecom_accepts_stream_creds():
    spec = get_spec("wecom")
    # Stream mode: bot_id + secret — no further shape constraint
    spec.validate({"bot_id": "bot123", "secret": "s3cr3t"})


def test_validate_wecom_rejects_missing_bot_id():
    spec = get_spec("wecom")
    with pytest.raises(ChannelConfigError, match="bot_id"):
        spec.validate({"secret": "s3cr3t"})


def test_validate_discord_rejects_short_token():
    spec = get_spec("discord")
    with pytest.raises(ChannelConfigError, match="token"):
        spec.validate({"token": "short"})


def test_validate_slack_rejects_token_without_xox_prefix():
    spec = get_spec("slack")
    with pytest.raises(ChannelConfigError, match="token"):
        spec.validate({"token": "abc123"})


def test_validate_slack_accepts_xoxb_token():
    spec = get_spec("slack")
    spec.validate({"token": "xoxb-abc-def-ghi"})


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


def test_mask_secrets_redacts_app_secret_keeping_last_4():
    spec = get_spec("feishu")
    masked = spec.mask_secrets({
        "app_id": "cli_visible",
        "app_secret": "thisIsTheSecretValueAbcd",
    })
    assert masked["app_id"] == "cli_visible"   # not a secret field
    assert masked["app_secret"] == "●●●●●●Abcd"


def test_mask_secrets_redacts_short_secret_to_placeholder():
    spec = get_spec("feishu")
    masked = spec.mask_secrets({"app_id": "x", "app_secret": "abc"})
    assert masked["app_secret"] == "●●●●"


def test_mask_secrets_handles_missing_secret_field():
    """Some optional secret fields (encrypt_key) may be absent — that's
    fine, just skip them rather than emitting empty masks."""
    spec = get_spec("feishu")
    masked = spec.mask_secrets({"app_id": "x", "app_secret": "longersecretvalue"})
    assert "encrypt_key" not in masked


def test_mask_secrets_preserves_non_string_values_untouched():
    """If something pushes a non-string into a secret field, leave it
    alone — masking is for diagnosing creds, not for sanitizing junk."""
    spec = get_spec("feishu")
    masked = spec.mask_secrets({"app_id": "x", "app_secret": 12345})  # weird shape
    assert masked["app_secret"] == 12345


def test_mask_secrets_redacts_telegram_token():
    spec = get_spec("telegram")
    masked = spec.mask_secrets({"token": "1234567890:ABCDEFghijklmnopQRSTuvwxyz"})
    assert masked["token"].startswith("●●●●●●")
    assert masked["token"].endswith("wxyz")


def test_mask_secrets_redacts_wecom_secret():
    spec = get_spec("wecom")
    masked = spec.mask_secrets({
        "bot_id": "bot-public",
        "secret": "supersecret-bot-secret",
    })
    assert masked["bot_id"] == "bot-public"     # not secret
    assert masked["secret"].startswith("●●●")


import asyncio

from hermes_cloud.app.channel_registry import _probe_weixin


def test_probe_weixin_missing_token_in_extra(tmp_path):
    res = asyncio.run(_probe_weixin({"account_id": "f974@im.bot"}, tmp_path))
    assert res.ok is False
    assert "token" in res.error


def test_probe_weixin_missing_account_id(tmp_path):
    res = asyncio.run(_probe_weixin({"token": "t"}, tmp_path))
    assert res.ok is False
    assert "account_id" in res.error


def test_probe_weixin_reads_token_from_extra(monkeypatch, tmp_path):
    import gateway.platforms.weixin as wx

    async def _fake_get_config(session, *, base_url, token, user_id, context_token):
        assert token == "tok-from-extra"
        assert base_url == "https://ilinkai.weixin.qq.com"
        return {"ret": 0}

    monkeypatch.setattr(wx, "_get_config", _fake_get_config)
    monkeypatch.setattr(wx, "_make_ssl_connector", lambda: None)

    extra = {
        "account_id": "f974@im.bot",
        "token": "tok-from-extra",
        "base_url": "https://ilinkai.weixin.qq.com",
        "user_id": "o9cq@im.wechat",
    }
    res = asyncio.run(_probe_weixin(extra, tmp_path))
    assert res.ok is True


def _probe_weixin_with_getconfig_resp(monkeypatch, tmp_path, resp):
    import gateway.platforms.weixin as wx

    async def _fake_get_config(session, *, base_url, token, user_id, context_token):
        return resp

    monkeypatch.setattr(wx, "_get_config", _fake_get_config)
    monkeypatch.setattr(wx, "_make_ssl_connector", lambda: None)
    extra = {"account_id": "f974@im.bot", "token": "t", "base_url": "https://x", "user_id": "o9cq@im.wechat"}
    return asyncio.run(_probe_weixin(extra, tmp_path))


def test_probe_weixin_typing_ticket_failure_is_connected(monkeypatch, tmp_path):
    # Cold getconfig probe (no conversation context) legitimately fails the
    # typing-ticket sub-RPC with ret=-4 — token authenticated, bot reachable,
    # sends work. Must be treated as CONNECTED, not a failure.
    res = _probe_weixin_with_getconfig_resp(
        monkeypatch, tmp_path,
        {"ret": -4, "errmsg": "GetTypingTicket rpc failed"},
    )
    assert res.ok is True


def test_probe_weixin_session_expired_is_failure(monkeypatch, tmp_path):
    res = _probe_weixin_with_getconfig_resp(monkeypatch, tmp_path, {"ret": -14})
    assert res.ok is False
    assert "过期" in res.error or "扫码" in res.error


def test_probe_weixin_rate_limited_is_failure(monkeypatch, tmp_path):
    res = _probe_weixin_with_getconfig_resp(monkeypatch, tmp_path, {"ret": -2})
    assert res.ok is False
    assert "限频" in res.error
