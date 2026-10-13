"""QR onboarding endpoints — /qr/start + /qr/poll for Feishu + WeChat."""
from __future__ import annotations

# Ensure hermes/ is on sys.path before importing the gateway namespace
# package. dev_server.py does this at runtime; tests need the parity.
import sys
from pathlib import Path
_HERMES_DIR = Path(__file__).resolve().parents[2] / "hermes"
if _HERMES_DIR.is_dir() and str(_HERMES_DIR) not in sys.path:
    sys.path.insert(0, str(_HERMES_DIR))

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# Pre-import the hermes adapter modules so mock.patch can locate them.
import plugins.platforms.feishu.adapter  # noqa: F401
import gateway.platforms.weixin  # noqa: F401


@dataclass
class _FakeUser:
    id: str
    is_admin: bool = False


@dataclass
class _FakeProfile:
    id: str
    user_id: str
    is_active: bool = True


class _FakeAuthStore:
    def __init__(self, users):
        self._u = {u.id: u for u in users}

    def authenticate_user(self, token):
        return token if token in self._u else None

    def get_user(self, uid):
        return self._u.get(uid)


class _FakeProfileStore:
    def __init__(self, profiles):
        self._p = {p.id: p for p in profiles}

    def get_profile(self, pid):
        return self._p.get(pid)

    def ensure_default_profile(self, *, user_id):
        return next(p for p in self._p.values() if p.user_id == user_id)


@pytest.fixture
def client(tmp_path: Path):
    cfg = AppConfig(
        internal_key="k",
        hermes_home_root=str(tmp_path),
        api_keys={"user-a": "user-a"},
    )
    app = create_app(cfg)
    app.state.auth_store = _FakeAuthStore([_FakeUser(id="user-a")])
    app.state.profile_store = _FakeProfileStore([
        _FakeProfile(id="p-a", user_id="user-a"),
    ])
    pm = MagicMock()
    pm.restart_profile = AsyncMock(return_value=9100)
    app.state.profile_manager = pm
    return TestClient(app)


def _auth(token="user-a"):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# /qr/start
# ---------------------------------------------------------------------------


def test_qr_start_rejects_non_qr_channel(client):
    resp = client.post(
        "/api/profiles/p-a/channels/telegram/qr/start",
        headers=_auth(),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "qr_not_supported"


def test_qr_start_unknown_channel_returns_404(client):
    resp = client.post(
        "/api/profiles/p-a/channels/nonexistent/qr/start",
        headers=_auth(),
    )
    assert resp.status_code == 404


def test_qr_start_feishu_returns_session_id_and_qr_url(client):
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        mock_start.return_value = {
            "device_code": "dc_abc",
            "qr_url": "https://applink.feishu.cn/code/abc?from=hermes",
            "user_code": "1234-5678",
            "interval": 5,
            "expire_in": 600,
            "domain": "feishu",
        }
        resp = client.post(
            "/api/profiles/p-a/channels/feishu/qr/start",
            headers=_auth(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["qr_url"].startswith("https://applink.feishu.cn")
    assert len(body["session_id"]) > 10
    assert body["interval"] == 5


def test_qr_start_feishu_respects_domain_override(client):
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        mock_start.return_value = {
            "device_code": "dc_x", "qr_url": "https://lark.x/y",
            "user_code": "u", "interval": 5, "expire_in": 600,
            "domain": "lark",
        }
        resp = client.post(
            "/api/profiles/p-a/channels/feishu/qr/start",
            headers=_auth(),
            json={"domain": "lark"},
        )
    assert resp.status_code == 200
    mock_start.assert_called_with(domain="lark")


def test_qr_start_weixin_returns_qrcode_url(client):
    with patch("gateway.platforms.weixin.qr_start_async") as mock_start:
        mock_start.return_value = {
            "qrcode": "hexabc",
            "qrcode_url": "https://wxapi.weixin.qq.com/qr/hexabc",
            "base_url": "https://ilinkai.weixin.qq.com",
            "expire_in": 480,
        }
        resp = client.post(
            "/api/profiles/p-a/channels/weixin/qr/start",
            headers=_auth(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["qr_url"].startswith("https://wxapi.weixin.qq.com")


def test_qr_start_propagates_hermes_failure_as_502(client):
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        mock_start.side_effect = RuntimeError("client_secret not supported")
        resp = client.post(
            "/api/profiles/p-a/channels/feishu/qr/start",
            headers=_auth(),
        )
    assert resp.status_code == 502
    assert "client_secret" in resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# /qr/poll
# ---------------------------------------------------------------------------


def _start_feishu_session(client, mock_start):
    """Helper: start a feishu session and return its session_id."""
    mock_start.return_value = {
        "device_code": "dc_abc",
        "qr_url": "https://applink.feishu.cn/code/abc",
        "user_code": "u",
        "interval": 5,
        "expire_in": 600,
        "domain": "feishu",
    }
    resp = client.post(
        "/api/profiles/p-a/channels/feishu/qr/start",
        headers=_auth(),
    )
    return resp.json()["session_id"]


def test_qr_poll_unknown_session_returns_404(client):
    resp = client.get(
        "/api/profiles/p-a/channels/feishu/qr/poll",
        headers=_auth(),
        params={"session_id": "bogus"},
    )
    assert resp.status_code == 404


def test_qr_poll_pending_returns_pending(client):
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        sid = _start_feishu_session(client, mock_start)
    with patch("plugins.platforms.feishu.adapter.qr_poll_once") as mock_poll:
        mock_poll.return_value = {"status": "pending"}
        resp = client.get(
            "/api/profiles/p-a/channels/feishu/qr/poll",
            headers=_auth(),
            params={"session_id": sid},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_qr_poll_completed_writes_config_yaml(client, tmp_path):
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        sid = _start_feishu_session(client, mock_start)
    with patch("plugins.platforms.feishu.adapter.qr_poll_once") as mock_poll:
        mock_poll.return_value = {
            "status": "completed",
            "creds": {
                "app_id": "cli_provisioned",
                "app_secret": "secret_provisioned",
                "domain": "feishu",
                "open_id": "ou_user",
                "bot_name": "hermes-personalagent-7",
                "bot_open_id": "ou_bot",
            },
            "domain": "feishu",
        }
        resp = client.get(
            "/api/profiles/p-a/channels/feishu/qr/poll",
            headers=_auth(),
            params={"session_id": sid},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    # Masked
    assert body["extra"]["app_id"] == "cli_provisioned"
    assert body["extra"]["app_secret"].startswith("●●●●")

    # Persisted to config.yaml
    from hermes_cloud.app.channel_config import read_channel_config
    extra = read_channel_config(tmp_path / "profiles" / "p-a", "feishu")
    assert extra["app_id"] == "cli_provisioned"
    assert extra["app_secret"] == "secret_provisioned"
    assert extra["bot_name"] == "hermes-personalagent-7"
    assert extra["connection_mode"] == "websocket"


def test_qr_poll_completed_restarts_profile(client):
    """On QR completion the creds land in config.yaml and the profile
    restarts so hermes' native FeishuAdapter connects the new bot."""
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        sid = _start_feishu_session(client, mock_start)
    with patch("plugins.platforms.feishu.adapter.qr_poll_once") as mock_poll:
        mock_poll.return_value = {
            "status": "completed",
            "creds": {"app_id": "x", "app_secret": "y", "domain": "feishu"},
            "domain": "feishu",
        }
        client.get(
            "/api/profiles/p-a/channels/feishu/qr/poll",
            headers=_auth(),
            params={"session_id": sid},
        )
    client.app.state.profile_manager.restart_profile.assert_awaited_with("p-a", user_id="user-a")


def test_qr_poll_denied_replays_then_clears(client):
    """Denied is terminal, but the session lingers for a ~60s grace
    window replaying "denied" — racing polls from the SPA's
    setInterval must not get a misleading 404 while clearTimers()
    propagates. After the window the session is dropped."""
    import time as _time
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        sid = _start_feishu_session(client, mock_start)
    with patch("plugins.platforms.feishu.adapter.qr_poll_once") as mock_poll:
        mock_poll.return_value = {"status": "denied"}
        resp = client.get(
            "/api/profiles/p-a/channels/feishu/qr/poll",
            headers=_auth(),
            params={"session_id": sid},
        )
    assert resp.json()["status"] == "denied"
    # Grace window: a racing poll still sees the terminal status (no 404)
    resp2 = client.get(
        "/api/profiles/p-a/channels/feishu/qr/poll",
        headers=_auth(),
        params={"session_id": sid},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "denied"
    # Past the window: one last replay drops the session…
    client.app.state.qr_sessions[sid]["terminal_until"] = _time.time() - 1
    resp3 = client.get(
        "/api/profiles/p-a/channels/feishu/qr/poll",
        headers=_auth(),
        params={"session_id": sid},
    )
    assert resp3.json()["status"] == "denied"
    # …and the next poll finally 404s
    resp4 = client.get(
        "/api/profiles/p-a/channels/feishu/qr/poll",
        headers=_auth(),
        params={"session_id": sid},
    )
    assert resp4.status_code == 404


def test_qr_poll_expired_clears_session(client):
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        sid = _start_feishu_session(client, mock_start)
    with patch("plugins.platforms.feishu.adapter.qr_poll_once") as mock_poll:
        mock_poll.return_value = {"status": "expired"}
        resp = client.get(
            "/api/profiles/p-a/channels/feishu/qr/poll",
            headers=_auth(),
            params={"session_id": sid},
        )
    assert resp.json()["status"] == "expired"


def test_qr_poll_session_mismatch_returns_403(client):
    """Trying to poll a feishu session against the weixin URL returns
    403 — prevents accidental session hijacking via URL guessing."""
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        sid = _start_feishu_session(client, mock_start)
    resp = client.get(
        "/api/profiles/p-a/channels/weixin/qr/poll",
        headers=_auth(),
        params={"session_id": sid},
    )
    assert resp.status_code == 403


def test_qr_poll_session_ttl_expires(client):
    """After expires_at passes, poll returns 'expired' and drops the
    session even if hermes' poll says 'pending'."""
    import time as _time
    with patch("plugins.platforms.feishu.adapter.qr_start") as mock_start:
        sid = _start_feishu_session(client, mock_start)
    # Force the session to be expired
    client.app.state.qr_sessions[sid]["expires_at"] = _time.time() - 1
    resp = client.get(
        "/api/profiles/p-a/channels/feishu/qr/poll",
        headers=_auth(),
        params={"session_id": sid},
    )
    assert resp.json()["status"] == "expired"


def test_qr_poll_weixin_redirect_updates_base_url(client):
    """iLink mid-flow redirect must update the session's base_url so
    subsequent polls hit the new shard host."""
    with patch("gateway.platforms.weixin.qr_start_async") as mock_start:
        mock_start.return_value = {
            "qrcode": "hexabc",
            "qrcode_url": "https://wxapi.weixin.qq.com/qr/hexabc",
            "base_url": "https://ilinkai.weixin.qq.com",
            "expire_in": 480,
        }
        resp = client.post(
            "/api/profiles/p-a/channels/weixin/qr/start",
            headers=_auth(),
        )
    sid = resp.json()["session_id"]

    with patch("gateway.platforms.weixin.qr_poll_once_async") as mock_poll:
        mock_poll.return_value = {
            "status": "redirect",
            "base_url": "https://ilinkai-bj.weixin.qq.com",
        }
        resp = client.get(
            "/api/profiles/p-a/channels/weixin/qr/poll",
            headers=_auth(),
            params={"session_id": sid},
        )
    # Treated as pending so SPA continues polling
    assert resp.json()["status"] == "pending"
    # And base_url stored for next poll
    assert client.app.state.qr_sessions[sid]["base_url"] == "https://ilinkai-bj.weixin.qq.com"
