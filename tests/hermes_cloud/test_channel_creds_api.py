"""REST endpoints for per-profile channel credentials.

Drives /api/profiles/{id}/channels via FastAPI TestClient. Verifies
RBAC (owner vs admin vs stranger), shape (404 unknown channel, 400 bad
extra), and the masked-secret round-trip (PUT after GET preserves the
real secret when the user only edits non-secret fields).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


# ---------------------------------------------------------------------------
# Test doubles for the auth + profile stores
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
    def __init__(self, users: list[_FakeUser]):
        self._users = {u.id: u for u in users}
        # API-keys are 1:1 with user_id for simplicity in tests.

    def authenticate_user(self, token: str) -> str | None:
        return token if token in self._users else None

    def get_user(self, user_id: str) -> _FakeUser | None:
        return self._users.get(user_id)


class _FakeProfileStore:
    def __init__(self, profiles: list[_FakeProfile]):
        self._profiles = {p.id: p for p in profiles}
        self._user_default: dict[str, _FakeProfile] = {}
        for p in profiles:
            self._user_default.setdefault(p.user_id, p)

    def get_profile(self, pid):
        return self._profiles.get(pid)

    def ensure_default_profile(self, *, user_id):
        return self._user_default[user_id]


# ---------------------------------------------------------------------------
# Fixture: app with owner=user-a, profile=p-a, admin user=user-admin
# ---------------------------------------------------------------------------


@pytest.fixture
def app_factory(tmp_path: Path):
    def make():
        cfg = AppConfig(
            internal_key="test-internal-key",
            hermes_home_root=str(tmp_path),
            api_keys={
                # Static API key map → Bearer "user-a" identifies user_id=user-a
                "user-a": "user-a",
                "user-admin": "user-admin",
                "stranger": "stranger",
            },
        )
        app = create_app(cfg)
        # Swap in fakes
        app.state.auth_store = _FakeAuthStore([
            _FakeUser(id="user-a", is_admin=False),
            _FakeUser(id="user-admin", is_admin=True),
            _FakeUser(id="stranger", is_admin=False),
        ])
        app.state.profile_store = _FakeProfileStore([
            _FakeProfile(id="p-a", user_id="user-a"),
            _FakeProfile(id="p-stranger", user_id="stranger"),
        ])
        # Channel writes restart the profile so hermes re-wires its
        # adapters — restart_profile is async, so stub it as AsyncMock.
        pm = MagicMock()
        pm.restart_profile = AsyncMock(return_value=9100)
        app.state.profile_manager = pm
        return app
    return make


@pytest.fixture
def client(app_factory):
    return TestClient(app_factory())


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_list_requires_auth(client):
    resp = client.get("/api/profiles/p-a/channels")
    assert resp.status_code == 401


def test_owner_can_list_channels(client):
    resp = client.get("/api/profiles/p-a/channels", headers=_auth("user-a"))
    assert resp.status_code == 200
    channels = {c["channel"] for c in resp.json()["channels"]}
    assert {"feishu", "weixin", "wecom", "telegram", "discord", "slack", "dingtalk"} <= channels


def test_admin_can_list_other_users_channels(client):
    resp = client.get("/api/profiles/p-a/channels", headers=_auth("user-admin"))
    assert resp.status_code == 200


def test_stranger_cannot_list_others_channels(client):
    resp = client.get("/api/profiles/p-a/channels", headers=_auth("stranger"))
    assert resp.status_code == 403


def test_unknown_profile_returns_404(client):
    resp = client.get("/api/profiles/p-nonexistent/channels", headers=_auth("user-a"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List shape
# ---------------------------------------------------------------------------


def test_list_marks_configured_false_when_no_creds(client):
    resp = client.get("/api/profiles/p-a/channels", headers=_auth("user-a"))
    for c in resp.json()["channels"]:
        assert c["configured"] is False
        assert "supports_qr" in c
        assert "display_name" in c


def test_list_marks_qr_capability_correctly(client):
    resp = client.get("/api/profiles/p-a/channels", headers=_auth("user-a"))
    by_name = {c["channel"]: c for c in resp.json()["channels"]}
    assert by_name["feishu"]["supports_qr"] is True
    assert by_name["weixin"]["supports_qr"] is True
    assert by_name["telegram"]["supports_qr"] is False
    # wecom/dingtalk 切 AI Bot stream 凭证后开启扫码绑定(2893393)
    assert by_name["wecom"]["supports_qr"] is True
    assert by_name["dingtalk"]["supports_qr"] is True


# ---------------------------------------------------------------------------
# PUT / GET / DELETE
# ---------------------------------------------------------------------------


def test_put_creates_then_get_returns_masked(client):
    resp = client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"app_id": "cli_visible", "app_secret": "veryLongSecretAbcd"},
    )
    assert resp.status_code == 200
    assert resp.json()["configured"] is True

    resp = client.get("/api/profiles/p-a/channels/feishu", headers=_auth("user-a"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["extra"]["app_id"] == "cli_visible"
    assert body["extra"]["app_secret"] == "●●●●●●Abcd"


def test_put_rejects_missing_required_field(client):
    resp = client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"app_id": "cli_x"},   # missing app_secret
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_channel_config"


def test_put_rejects_unknown_domain(client):
    resp = client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"app_id": "x", "app_secret": "s", "domain": "smoke-signals"},
    )
    assert resp.status_code == 400


def test_put_unknown_channel_returns_404(client):
    resp = client.put(
        "/api/profiles/p-a/channels/not-a-channel",
        headers=_auth("user-a"),
        json={"foo": "bar"},
    )
    assert resp.status_code == 404


def test_put_masked_value_preserves_existing_secret(client):
    """SPA flow: GET returns masked secret; user edits non-secret field
    and PUTs back the form. The masked value must not overwrite the
    real secret."""
    # 1. Initial save with real secret
    client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"app_id": "cli_x", "app_secret": "realSecretAbcd"},
    )
    # 2. GET returns masked
    masked = client.get(
        "/api/profiles/p-a/channels/feishu", headers=_auth("user-a"),
    ).json()["extra"]
    assert masked["app_secret"].startswith("●●●●")
    # 3. User edits domain, re-PUTs the masked GET response (simulates
    # SPA form submit without re-typing secret)
    masked["domain"] = "lark"
    client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json=masked,
    )
    # 4. The on-disk secret is still the real one — verify by writing
    # something that needs it (we can only see it through masking now,
    # so probe the underlying read via direct module)
    from hermes_cloud.app.channel_config import read_channel_config
    extra = read_channel_config(Path(client.app.state.config.hermes_home_root) / "profiles" / "p-a", "feishu")
    assert extra["app_secret"] == "realSecretAbcd"
    assert extra["domain"] == "lark"


def test_put_partial_update_preserves_unsent_fields(client):
    """PUT-ing only ``domain`` keeps ``app_id`` and ``app_secret``
    from the prior save. (Behaves like PATCH semantics.)"""
    client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"app_id": "cli_x", "app_secret": "s", "domain": "feishu"},
    )
    client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"domain": "lark"},  # partial
    )
    resp = client.get("/api/profiles/p-a/channels/feishu", headers=_auth("user-a"))
    extra = resp.json()["extra"]
    assert extra["app_id"] == "cli_x"
    assert extra["domain"] == "lark"


def test_delete_removes_channel(client):
    client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"app_id": "x", "app_secret": "s"},
    )
    resp = client.delete("/api/profiles/p-a/channels/feishu", headers=_auth("user-a"))
    assert resp.status_code == 200
    assert resp.json()["configured"] is False

    resp = client.get("/api/profiles/p-a/channels/feishu", headers=_auth("user-a"))
    assert resp.json()["configured"] is False
    assert resp.json()["extra"] == {}


def test_put_restarts_profile_so_adapter_rewires(client):
    """A credential write restarts the subprocess so hermes' native
    adapter connects the (re)configured bot — adapters are wired at
    gateway boot, not hot-reloaded."""
    client.put(
        "/api/profiles/p-a/channels/feishu",
        headers=_auth("user-a"),
        json={"app_id": "x", "app_secret": "s"},
    )
    client.app.state.profile_manager.restart_profile.assert_awaited_with("p-a", user_id="user-a")


# ---------------------------------------------------------------------------
# Test endpoint
# ---------------------------------------------------------------------------


def test_test_endpoint_requires_configured_channel(client):
    resp = client.post(
        "/api/profiles/p-a/channels/feishu/test", headers=_auth("user-a"),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "not_configured"


def test_test_endpoint_501_for_channel_without_probe(client):
    """Channels with no probe (wecom/discord/slack/dingtalk) return
    501 ``probe_unsupported`` — the SPA hides their "test connection"
    button via the spec's ``supports_probe=False``. Feishu/Telegram/
    WeChat have real probes (covered in test_channel_registry)."""
    # wecom creds = AI Bot stream (bot_id+secret),旧 corp_id/agent_id 已废
    client.put(
        "/api/profiles/p-a/channels/wecom",
        headers=_auth("user-a"),
        json={"bot_id": "bot123", "secret": "s"},
    )
    resp = client.post(
        "/api/profiles/p-a/channels/wecom/test", headers=_auth("user-a"),
    )
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "probe_unsupported"
