"""Per-profile channel credentials, stored in ``config.yaml``.

Implements the storage layer for the per-user-channel-bot architecture:
each cloud user's channel credentials (Feishu app_id/secret, Telegram
bot token, WeCom corp_id+secret, etc.) live in their hermes profile's
``config.yaml`` under ``platforms.<channel>.extra``. Hermes' native
adapter (FeishuAdapter / WeixinAdapter / TelegramAdapter / ...) reads
this YAML on every request and connects directly — no cloud-side
bridge, no relay, no DB row.

The storage boundary is filesystem permissions (dir 700 / file 600,
same as hermes-CLI single-user). No envelope encryption layer; the
trust model is "anyone with shell access to the cloud host can read
the secrets" — which is also true for DB credentials, env vars, and
TLS private keys on the same host.

See ``docs/superpowers/plans/2026-05-19-channel-per-user-bot.md``.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)


def read_profile_config(profile_dir: Path) -> Dict[str, Any]:
    """Load ``<profile_dir>/config.yaml`` into a dict.

    Returns ``{}`` for any failure (missing file, malformed YAML, IO
    error) — callers see absence as "no config", which is the right
    default for both per-channel reads and write-then-merge updates.
    """
    path = profile_dir / "config.yaml"
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        logger.warning(
            "channel_config: failed to parse %s, treating as empty: %s",
            path, exc,
        )
        return {}


def write_profile_config(profile_dir: Path, data: Dict[str, Any]) -> None:
    """Write the whole config.yaml atomically with restricted permissions.

    The directory is created with mode 700 and the file is written at
    mode 600 if newly created. We don't tighten existing files because
    the user may have intentionally relaxed perms; we just don't loosen
    them either.

    Uses ``yaml.dump(allow_unicode=True)`` so Chinese / Japanese /
    Korean characters in prompts and bot names stay readable in the
    file rather than being escaped.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(profile_dir, 0o700)
    except OSError:
        # Best-effort — on Windows or networked filesystems chmod may
        # be a no-op or unsupported.
        pass
    path = profile_dir / "config.yaml"
    payload = yaml.dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False,
    )
    is_new = not path.exists()
    path.write_text(payload)
    if is_new:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def read_channel_config(profile_dir: Path, channel: str) -> Dict[str, Any]:
    """Return the ``platforms.<channel>.extra`` dict for one channel.

    Empty dict if the profile has no config, no platforms section, no
    entry for this channel, or no ``extra`` block under it. The return
    shape is always ``dict[str, Any]`` so callers can ``.get(field)``
    without None-checks.
    """
    data = read_profile_config(profile_dir)
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        return {}
    channel_entry = platforms.get(channel)
    if not isinstance(channel_entry, dict):
        return {}
    extra = channel_entry.get("extra")
    if not isinstance(extra, dict):
        return {}
    return extra


def write_channel_config(
    profile_dir: Path, channel: str, extra: Dict[str, Any],
) -> None:
    """Replace ``platforms.<channel>.extra`` atomically.

    Empty ``extra`` deletes the channel entry entirely (disconnect).
    Preserves all other channels and top-level config keys (model,
    agent.system_prompt, custom_providers, etc.) — this is a surgical
    update, not a full rewrite.

    Sets ``enabled: true`` whenever extra is non-empty so hermes'
    ``GatewayConfig.from_dict`` activates the adapter. Disconnect is
    "remove the whole channel entry" rather than "set enabled: false"
    so hermes doesn't see a half-configured platform on startup.
    """
    data = read_profile_config(profile_dir)
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
        data["platforms"] = platforms
    if not extra:
        platforms.pop(channel, None)
    else:
        # Preserve a previously-set home_channel (the cron-delivery
        # target) across a creds update — it sits beside ``extra``, and
        # this function replaces the whole channel entry.
        prev = platforms.get(channel)
        entry: Dict[str, Any] = {"enabled": True, "extra": dict(extra)}
        if isinstance(prev, dict) and isinstance(prev.get("home_channel"), dict):
            entry["home_channel"] = prev["home_channel"]
        platforms[channel] = entry
    write_profile_config(profile_dir, data)


def set_home_channel(
    profile_dir: Path, channel: str, chat_id: str, *, name: str = "Home",
) -> None:
    """Set ``platforms.<channel>.home_channel`` — the cron-delivery target.

    hermes' native scheduler resolves a bare ``deliver="<channel>"`` to
    this chat id (the bot owner's). Writing it into the profile's own
    ``config.yaml`` means the profile's hermes subprocess knows where to
    deliver proactive pushes by itself — no cloud-injected
    ``*_HOME_CHANNEL`` env, no ``channel_bindings`` table.

    No-op when ``chat_id`` is empty (owner identity not yet known).
    """
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return
    data = read_profile_config(profile_dir)
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
        data["platforms"] = platforms
    entry = platforms.get(channel)
    if not isinstance(entry, dict):
        entry = {"enabled": True, "extra": {}}
        platforms[channel] = entry
    entry["home_channel"] = {
        "platform": channel, "chat_id": chat_id, "name": name,
    }
    write_profile_config(profile_dir, data)


# ---------------------------------------------------------------------------
# Email channel — the one channel hermes configures from env vars, not YAML.
# ---------------------------------------------------------------------------
# hermes' EmailAdapter reads EMAIL_ADDRESS / EMAIL_PASSWORD / EMAIL_IMAP_HOST
# / ... straight from os.environ (see the official email channel docs and
# gateway/platforms/email.py — unlike Feishu/WeChat it does NOT read
# config.yaml). hermes loads ``<HERMES_HOME>/.env`` on startup
# (load_hermes_dotenv, override=True), and each profile's gateway runs with
# HERMES_HOME=<profile_dir> — so the profile's own ``.env`` IS its per-bot
# email config. The SPA channel form uses the lowercase field names below;
# we map them to the EMAIL_* env keys the adapter expects.

_EMAIL_ENV_FIELDS: Dict[str, str] = {
    "address": "EMAIL_ADDRESS",
    "password": "EMAIL_PASSWORD",
    "imap_host": "EMAIL_IMAP_HOST",
    "imap_port": "EMAIL_IMAP_PORT",
    "smtp_host": "EMAIL_SMTP_HOST",
    "smtp_port": "EMAIL_SMTP_PORT",
    "poll_interval": "EMAIL_POLL_INTERVAL",
    "allowed_users": "EMAIL_ALLOWED_USERS",
    "home_address": "EMAIL_HOME_ADDRESS",
    "owner_users": "EMAIL_OWNER_USERS",
    "persona": "EMAIL_PERSONA",
    "signature": "EMAIL_SIGNATURE",
    "customer_reply_guide": "EMAIL_CUSTOMER_REPLY_GUIDE",
}


def read_email_channel_config(profile_dir: Path) -> Dict[str, Any]:
    """Return the email channel's config read from ``<profile_dir>/.env``.

    Keyed by the SPA-facing lowercase field names (``address``,
    ``imap_host``, ...), not the ``EMAIL_*`` env keys. Empty dict when
    the file is missing or holds no ``EMAIL_*`` vars.
    """
    path = profile_dir / ".env"
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values
        raw = dotenv_values(str(path))
    except Exception as exc:
        logger.warning("channel_config: failed to read %s: %s", path, exc)
        return {}
    out: Dict[str, Any] = {}
    for field_name, env_key in _EMAIL_ENV_FIELDS.items():
        val = raw.get(env_key)
        if val:
            out[field_name] = val
    return out


def write_email_channel_config(
    profile_dir: Path, extra: Dict[str, Any],
) -> None:
    """Write the email channel's config into ``<profile_dir>/.env``.

    Only the ``EMAIL_*`` keys are touched — every other line in the file
    is preserved. Empty ``extra`` removes all ``EMAIL_*`` keys
    (disconnect). The profile's gateway picks the change up on its next
    restart, since hermes loads this ``.env`` at startup.
    """
    from dotenv import set_key, unset_key

    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(profile_dir, 0o700)
    except OSError:
        pass
    path = profile_dir / ".env"
    if not path.exists():
        path.touch()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    for field_name, env_key in _EMAIL_ENV_FIELDS.items():
        val = extra.get(field_name)
        if val is None or str(val).strip() == "":
            unset_key(str(path), env_key)
        else:
            set_key(str(path), env_key, str(val))
