"""QR onboarding endpoints — /qr/start + /qr/poll for DingTalk + WeCom.

Tests mirror the fixture approach from test_channel_qr_endpoints.py (feishu/weixin).
dingtalk primitives are SYNC; wecom primitives are ASYNC (AsyncMock).
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# Pre-import the modules that will be patched so mock.patch can locate them.
import hermes_cli.dingtalk_auth  # noqa: F401
import hermes_cloud.app.qr.wecom_qr  # noqa: F401


# ---------------------------------------------------------------------------
# Shared fake data-store stubs (mirrored verbatim from test_channel_qr_endpoints.py)
# ---------------------------------------------------------------------------

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
def client(tmp_path):
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
# Helper: derive the profile dir from a client (mirrors _profile_dir in main)
# ---------------------------------------------------------------------------

def _profile_dir(client, profile_id="p-a"):
    """Return the profile directory that _write_channel_config writes to."""
    from pathlib import Path
    cfg = client.app.extra.get("cfg") if hasattr(client.app, "extra") else None
    # Fall back: derive from the TestClient fixture's tmp_path via app config.
    # The AppConfig hermes_home_root was set to tmp_path in the fixture.
    # _profile_dir(pid) = hermes_home_root / "profiles" / pid
    root = client.app.state.__dict__.get("_hermes_home_root")
    if root:
        return Path(root) / "profiles" / profile_id
    # Try to read it from the app config stored by create_app.
    # We expose it by reading back through AppConfig equality.
    # Simplest: iterate all TestClient.app route handlers — too fragile.
    # Instead, look at what was passed to AppConfig in the fixture.
    # Since we can't easily retrieve it, reconstruct from the client app's
    # lifespan state. The fixture always sets hermes_home_root=str(tmp_path),
    # and qr_sessions exists after app init — find the path via a completed poll.
    raise RuntimeError("cannot derive profile dir — use the tmp_path fixture directly")


# ---------------------------------------------------------------------------
# DingTalk — sync primitives
# ---------------------------------------------------------------------------

class TestDingTalkQR:
    def test_qr_start_returns_session_id_and_qr_url(self, client):
        with patch("hermes_cli.dingtalk_auth.begin_registration") as mock_begin:
            mock_begin.return_value = {
                "device_code": "DC1",
                "verification_uri_complete": "https://dingtalk/qr",
                "expires_in": 7200,
                "interval": 3,
            }
            resp = client.post(
                "/api/profiles/p-a/channels/dingtalk/qr/start",
                headers=_auth(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "session_id" in body
        assert len(body["session_id"]) > 10
        assert body["qr_url"] == "https://dingtalk/qr"
        assert body["interval"] == 3

    def test_qr_poll_completed_writes_creds_and_returns_masked(
        self, client, tmp_path
    ):
        # Step 1 — start
        with patch("hermes_cli.dingtalk_auth.begin_registration") as mock_begin:
            mock_begin.return_value = {
                "device_code": "DC1",
                "verification_uri_complete": "https://dingtalk/qr",
                "expires_in": 7200,
                "interval": 3,
            }
            start_resp = client.post(
                "/api/profiles/p-a/channels/dingtalk/qr/start",
                headers=_auth(),
            )
        assert start_resp.status_code == 200
        sid = start_resp.json()["session_id"]

        # Step 2 — poll → completed (SYNC mock)
        with patch("hermes_cli.dingtalk_auth.poll_registration") as mock_poll:
            mock_poll.return_value = {
                "status": "SUCCESS",
                "client_id": "CID",
                "client_secret": "CSEC",
                "fail_reason": None,
            }
            poll_resp = client.get(
                "/api/profiles/p-a/channels/dingtalk/qr/poll",
                headers=_auth(),
                params={"session_id": sid},
            )
        assert poll_resp.status_code == 200
        body = poll_resp.json()
        assert body["status"] == "completed"
        assert "extra" in body
        assert body["extra"]["client_id"] == "CID"
        assert body["extra"]["client_secret"].startswith("●●●●")

        # Step 3 — verify persisted to config.yaml
        from hermes_cloud.app.channel_config import read_channel_config
        profile_dir = tmp_path / "profiles" / "p-a"
        extra = read_channel_config(profile_dir, "dingtalk")
        assert extra["client_id"] == "CID"
        assert extra["client_secret"] == "CSEC"

    def test_qr_poll_transient_network_error_stays_pending(self, client):
        """A transient RegistrationError (e.g. oapi.dingtalk.com connect
        timeout) during poll must NOT 500 — it should be swallowed as
        ``pending`` so the SPA keeps polling instead of aborting the scan."""
        from hermes_cli.dingtalk_auth import RegistrationError
        with patch("hermes_cli.dingtalk_auth.begin_registration") as mock_begin:
            mock_begin.return_value = {
                "device_code": "DC2",
                "verification_uri_complete": "https://dingtalk/qr",
                "expires_in": 7200,
                "interval": 3,
            }
            start_resp = client.post(
                "/api/profiles/p-a/channels/dingtalk/qr/start", headers=_auth(),
            )
        sid = start_resp.json()["session_id"]

        with patch("hermes_cli.dingtalk_auth.poll_registration") as mock_poll:
            mock_poll.side_effect = RegistrationError("connect timeout")
            poll_resp = client.get(
                "/api/profiles/p-a/channels/dingtalk/qr/poll",
                headers=_auth(), params={"session_id": sid},
            )
        assert poll_resp.status_code == 200
        assert poll_resp.json()["status"] == "pending"


# ---------------------------------------------------------------------------
# WeCom — async primitives
# ---------------------------------------------------------------------------

class TestWeComQR:
    def test_qr_start_returns_session_id_and_qr_url(self, client):
        async def _fake_qr_start():
            return {
                "scode": "SC9",
                "qr_url": "https://work.weixin.qq.com/ai/qc/gen?source=hermes&scode=SC9",
                "expires_in": 300,
            }

        with patch(
            "hermes_cloud.app.qr.wecom_qr.qr_start",
            new=_fake_qr_start,
        ):
            resp = client.post(
                "/api/profiles/p-a/channels/wecom/qr/start",
                headers=_auth(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "session_id" in body
        assert len(body["session_id"]) > 10
        assert body["qr_url"].startswith("https://work.weixin.qq.com")

    def test_qr_poll_completed_writes_creds_and_returns_masked(
        self, client, tmp_path
    ):
        # Step 1 — start
        async def _fake_qr_start():
            return {
                "scode": "SC9",
                "qr_url": "https://work.weixin.qq.com/ai/qc/gen?source=hermes&scode=SC9",
                "expires_in": 300,
            }

        with patch(
            "hermes_cloud.app.qr.wecom_qr.qr_start",
            new=_fake_qr_start,
        ):
            start_resp = client.post(
                "/api/profiles/p-a/channels/wecom/qr/start",
                headers=_auth(),
            )
        assert start_resp.status_code == 200
        sid = start_resp.json()["session_id"]

        # Step 2 — poll → completed (ASYNC mock)
        async def _fake_qr_poll_once(scode):
            assert scode == "SC9"
            return {
                "status": "completed",
                "creds": {"bot_id": "B9", "secret": "S9"},
            }

        with patch(
            "hermes_cloud.app.qr.wecom_qr.qr_poll_once",
            new=_fake_qr_poll_once,
        ):
            poll_resp = client.get(
                "/api/profiles/p-a/channels/wecom/qr/poll",
                headers=_auth(),
                params={"session_id": sid},
            )
        assert poll_resp.status_code == 200
        body = poll_resp.json()
        assert body["status"] == "completed"
        assert "extra" in body
        assert body["extra"]["bot_id"] == "B9"
        assert body["extra"]["secret"].startswith("●●●●")

        # Step 3 — verify persisted to config.yaml
        from hermes_cloud.app.channel_config import read_channel_config
        profile_dir = tmp_path / "profiles" / "p-a"
        extra = read_channel_config(profile_dir, "wecom")
        assert extra["bot_id"] == "B9"
        assert extra["secret"] == "S9"

    def test_qr_poll_transient_error_kept_polling(self, client):
        """A transient provider-side poll error (e.g. Feishu/WeChat
        'transport: The read operation timed out') must be swallowed as
        ``pending`` so the SPA keeps polling instead of aborting the scan."""
        async def _fake_qr_start():
            return {"scode": "SCT", "qr_url": "https://work.weixin.qq.com/ai/qc/gen?scode=SCT",
                    "expires_in": 300}

        async def _fake_poll_transient(scode):
            return {"status": "error", "message": "transport: The read operation timed out"}

        with patch("hermes_cloud.app.qr.wecom_qr.qr_start", new=_fake_qr_start):
            sid = client.post("/api/profiles/p-a/channels/wecom/qr/start",
                              headers=_auth()).json()["session_id"]
        with patch("hermes_cloud.app.qr.wecom_qr.qr_poll_once", new=_fake_poll_transient):
            r = client.get("/api/profiles/p-a/channels/wecom/qr/poll",
                           headers=_auth(), params={"session_id": sid})
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
