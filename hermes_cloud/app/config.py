import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger(__name__)


_TRUTHY = {"1", "true", "yes", "on"}


def _env_bool(value: Optional[str]) -> bool:
    """Interpret an env-var string as bool; empty / missing → False."""
    return (value or "").strip().lower() in _TRUTHY


def spark_env_values() -> dict[str, Any]:
    """Load ``SPARK_*`` env vars into AppConfig kwargs.

    Training jobs run on the NVIDIA DGX Spark host. hermes_cloud dispatches
    them over SSH; the Spark box is not on the public internet but reachable
    from the Mac Studio running this process. ``SPARK_SSH_PROXY_JUMP`` is
    used verbatim as paramiko's ``sock`` proxy spec (e.g.
    ``kenicrm@keni.kacrm.com``) when hermes_cloud runs off-host and needs to
    bounce through the Mac Studio; leave empty when this process runs ON the
    Mac Studio and can reach Spark directly via Bonjour.

    No env vars set → training backend is disabled (dispatch raises).
    """
    host = (os.environ.get("SPARK_SSH_HOST") or "").strip() or None
    user = (os.environ.get("SPARK_SSH_USER") or "kenimodel").strip()
    port_raw = (os.environ.get("SPARK_SSH_PORT") or "22").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 22
        logger.warning("SPARK_SSH_PORT=%r not an int, falling back to 22", port_raw)
    key_path = (os.environ.get("SPARK_SSH_KEY_PATH") or "").strip() or None
    password = os.environ.get("SPARK_SSH_PASSWORD") or None
    proxy_jump = (os.environ.get("SPARK_SSH_PROXY_JUMP") or "").strip() or None
    workdir = (os.environ.get("SPARK_WORKDIR") or "~/hermes-train").strip()
    venv_python = (
        os.environ.get("SPARK_VENV_PYTHON") or "~/.venv/bin/python"
    ).strip()
    mac_adapter_dir = (
        os.environ.get("MAC_ADAPTER_DIR") or "~/.hermes/adapters"
    ).strip()

    if host is None:
        logger.info(
            "SPARK_SSH_HOST unset; rl training backend will be disabled"
        )

    return {
        "spark_ssh_host": host,
        "spark_ssh_user": user,
        "spark_ssh_port": port,
        "spark_ssh_key_path": key_path,
        "spark_ssh_password": password,
        "spark_ssh_proxy_jump": proxy_jump,
        "spark_workdir": workdir,
        "spark_venv_python": venv_python,
        "mac_adapter_dir": mac_adapter_dir,
    }


def cloud_orchestrate_specialists_enabled() -> bool:
    """v3.3.1 feature flag — historical gate for the cloud-orchestrator
    specialist paths.

    Charter v2 Phase 2 (2026-05-05): the paths this flag used to gate
    were removed entirely along with the four folded specialists
    (intake / email_drafter / coach / compliance) and the
    ``event_router`` module. The flag is now effectively a no-op; the
    function is kept so existing env-reading boot code and the
    AppConfig field below stay backwards-compatible.
    """
    return _env_bool(os.environ.get("CLOUD_ORCHESTRATE_SPECIALISTS"))


@dataclass(frozen=True)
class AppConfig:
    service_name: str = "hermes-cloud"

    # SQLAlchemy database URL for auth + channel bindings.
    # None → everything in-memory (dev/test).
    session_store_url: str | None = None

    # HMAC secret for signed session cookies.
    # None → fallback to ?api_key= query-param (dev only).
    session_cookie_secret: str | None = None

    # Allow public self-registration via /auth/register.
    allow_registration: bool = False

    # Shared secret for cloud → per-user hermes process auth.
    # Must match API_SERVER_KEY written to each profile's config.yaml.
    internal_key: str = "change-me-in-production"

    # TCP port range for per-user hermes gateway processes.
    port_range_start: int = 9100
    port_range_end: int = 9999

    # 0 = processes run permanently (crashed ones are still reaped and restarted on demand).
    idle_timeout_seconds: float = 0.0

    # Path to the `hermes` executable.  Defaults to "hermes" (from PATH).
    hermes_bin: str = "hermes"

    # Override root of hermes profiles directory.
    # None → ~/.hermes  (profiles live at ~/.hermes/profiles/{user_id}/).
    hermes_home_root: Optional[str] = None

    # Channel-binding webhook HMAC secret.
    # Falls back to internal_key when None.
    channel_bind_webhook_secret: str | None = None

    # Static API key → user_id overrides for programmatic / legacy access.
    api_keys: dict[str, str] = field(default_factory=dict)

    # ── v3.3.1 feature flag (historical, no-op since Phase 2) ───────────
    # Originally gated three cloud-orchestrator paths that have since
    # been removed along with the folded specialists (Charter v2 Phase
    # 2, 2026-05-05). Kept here so callers reading
    # ``CLOUD_ORCHESTRATE_SPECIALISTS`` from env don't error out.
    cloud_orchestrate_specialists: bool = False

    # ── Other channels: webhook-only signature secrets ────────────────────
    # Slack Events API — signing secret from slack.com/apps → Basic Info.
    slack_signing_secret: Optional[str] = None
    # Telegram — arbitrary opaque token echoed in the
    # ``X-Telegram-Bot-Api-Secret-Token`` header.
    telegram_webhook_secret: Optional[str] = None
    # Discord Interactions — application's Ed25519 public key (hex).
    discord_public_key: Optional[str] = None
    # DingTalk — either outgoing-robot HMAC (app_secret only) or
    # full event subscription (token + aes_key + app_key).
    dingtalk_app_secret: Optional[str] = None
    dingtalk_token: Optional[str] = None
    dingtalk_encoding_aes_key: Optional[str] = None
    dingtalk_app_key: Optional[str] = None
    # WeCom (企业微信) — token + EncodingAESKey + corp_id.
    wecom_token: Optional[str] = None
    wecom_encoding_aes_key: Optional[str] = None
    wecom_corp_id: Optional[str] = None
    # Inbound email — shared secret matched via Bearer or Basic auth.
    email_inbound_secret: Optional[str] = None

    # ── Training backend (NVIDIA DGX Spark) ───────────────────────────────
    # hermes_cloud dispatches SFT / GRPO training jobs to the Spark host over
    # SSH. When ``spark_ssh_host`` is None the training orchestrator refuses
    # to run (no silent fallback to "train locally on CPU").
    spark_ssh_host: Optional[str] = None
    spark_ssh_user: str = "kenimodel"
    spark_ssh_port: int = 22
    spark_ssh_key_path: Optional[str] = None
    spark_ssh_password: Optional[str] = None
    # Optional ProxyJump spec (``user@jump.example.com``) so hermes_cloud
    # running off the local network can still reach Spark. On the Mac Studio
    # this should be None — Spark is reachable directly via Bonjour.
    spark_ssh_proxy_jump: Optional[str] = None
    # Remote directory convention (training scripts, datasets, adapters live
    # under here on Spark).
    spark_workdir: str = "~/hermes-train"
    # Interpreter used for training jobs on Spark.
    spark_venv_python: str = "~/.venv/bin/python"
    # Local directory on this host where scp'd adapters are mirrored for
    # inference. Must be readable by the hermes subprocess.
    mac_adapter_dir: str = "~/.hermes/adapters"
