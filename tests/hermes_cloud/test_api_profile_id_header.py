"""HTTP 端到端：user-config 端点是否按 (user, profile) 正确隔离配置，并尊重
``X-Profile-Id`` 头（Charter v2 §简化原则：1 user = 1 profile）。

v2 下每个用户只有一个 profile（默认，``id == username``）。"按 profile 隔离"
退化为"按用户隔离"——用两个真实用户各自的默认 profile 来验证 config 不互相
污染；``X-Profile-Id`` 头只接受调用者自己的 username（== 其默认 profile），
指向他人的 profile 一律 403。

覆盖的端点：
  - GET/PUT /api/user-config/model
  - GET/PUT /api/user-config/toolsets
  - GET/POST /api/user-config/agents
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# ``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
# dropped (commit 367e344). These X-Profile-Id routing tests need
# ``app.state.profile_store``, wired by ``create_app`` only when
# ``session_store_url`` reaches a real PG.
pytestmark = pytest.mark.requires_real_pg


def _make_app(tmp_path, users):
    """Create an app wired to the real CRM PG, seeding each ``pg_users``
    namespace's token → decimal uid into ``api_keys``."""
    from hermes.crm.db import _resolve_dsn

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return create_app(
        AppConfig(
            api_keys={u.token: u.uid for u in users},
            internal_key="internal-secret",
            session_store_url=dsn,
            hermes_home_root=str(tmp_path / "hermes_home"),
        )
    )


# ---------------------------------------------------------------------------
# model 配置
# ---------------------------------------------------------------------------

def test_model_config_is_isolated_between_users(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app = _make_app(tmp_path, [alice, bob])
    with TestClient(app) as client:
        # 写 alice 的默认 profile
        r = client.put(
            "/api/user-config/model",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"model": "alice-m", "api_key": "ak", "base_url": "https://a"},
        )
        assert r.status_code == 200

        # 写 bob 的默认 profile
        r = client.put(
            "/api/user-config/model",
            headers={"Authorization": f"Bearer {bob.token}"},
            json={"model": "bob-m", "api_key": "bk", "base_url": "https://b"},
        )
        assert r.status_code == 200

        got_alice = client.get(
            "/api/user-config/model",
            headers={"Authorization": f"Bearer {alice.token}"},
        ).json()
        got_bob = client.get(
            "/api/user-config/model",
            headers={"Authorization": f"Bearer {bob.token}"},
        ).json()

    assert got_alice["model"] == "alice-m"
    assert got_bob["model"] == "bob-m"


def test_own_profile_header_matches_default(tmp_path, pg_users):
    """显式带上自己的 username 作为 X-Profile-Id，与不带头（默认 profile）
    读到的是同一份配置。"""
    (alice,) = pg_users(1)
    app = _make_app(tmp_path, [alice])
    with TestClient(app) as client:
        client.put(
            "/api/user-config/model",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"model": "alice-m", "api_key": "ak", "base_url": "https://a"},
        )
        via_header = client.get(
            "/api/user-config/model",
            headers={
                "Authorization": f"Bearer {alice.token}",
                "X-Profile-Id": alice.username,
            },
        )
    assert via_header.status_code == 200
    assert via_header.json()["model"] == "alice-m"


# ---------------------------------------------------------------------------
# toolsets 配置（= UI 里的 "技能"）
# ---------------------------------------------------------------------------

def test_toolsets_config_is_isolated_between_users(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app = _make_app(tmp_path, [alice, bob])
    with TestClient(app) as client:
        client.put(
            "/api/user-config/toolsets",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"toolsets": ["web"]},
        )
        client.put(
            "/api/user-config/toolsets",
            headers={"Authorization": f"Bearer {bob.token}"},
            json={"toolsets": ["file", "exec"]},
        )

        alice_ts = client.get(
            "/api/user-config/toolsets",
            headers={"Authorization": f"Bearer {alice.token}"},
        ).json()
        bob_ts = client.get(
            "/api/user-config/toolsets",
            headers={"Authorization": f"Bearer {bob.token}"},
        ).json()

    assert alice_ts["toolsets"] == ["web"]
    assert bob_ts["toolsets"] == ["file", "exec"]


# ---------------------------------------------------------------------------
# agents 配置（system_prompt / memory_enabled）
# ---------------------------------------------------------------------------

def test_agents_config_is_isolated_between_users(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app = _make_app(tmp_path, [alice, bob])
    with TestClient(app) as client:
        client.post(
            "/api/user-config/agents",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"system_prompt": "alice sys", "memory_enabled": True},
        )
        client.post(
            "/api/user-config/agents",
            headers={"Authorization": f"Bearer {bob.token}"},
            json={"system_prompt": "bob sys", "memory_enabled": False},
        )

        alice_ag = client.get(
            "/api/user-config/agents",
            headers={"Authorization": f"Bearer {alice.token}"},
        ).json()
        bob_ag = client.get(
            "/api/user-config/agents",
            headers={"Authorization": f"Bearer {bob.token}"},
        ).json()

    assert alice_ag["system_prompt"] == "alice sys"
    assert alice_ag["memory_enabled"] is True
    assert bob_ag["system_prompt"] == "bob sys"
    assert bob_ag["memory_enabled"] is False


# ---------------------------------------------------------------------------
# 403 路径：X-Profile-Id 指向他人的 profile
# ---------------------------------------------------------------------------

def test_wrong_profile_id_returns_403(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app = _make_app(tmp_path, [alice, bob])
    with TestClient(app) as client:
        # 懒建两人的默认 profile
        client.get(
            "/api/user-config/model",
            headers={"Authorization": f"Bearer {alice.token}"},
        )
        client.get(
            "/api/user-config/model",
            headers={"Authorization": f"Bearer {bob.token}"},
        )

        # alice 持 bob 的 profile_id 读 config → 403
        r = client.get(
            "/api/user-config/model",
            headers={
                "Authorization": f"Bearer {alice.token}",
                "X-Profile-Id": bob.username,
            },
        )
    assert r.status_code == 403
