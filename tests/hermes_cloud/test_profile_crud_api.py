"""Profile CRUD HTTP 端点（Charter v2 §简化原则：1 user = 1 profile）。

UI 上"个人信息 / Agent"页对应：

  GET    /api/profiles                — 列当前 user 的 profile（恒为 1 个：默认）
  POST   /api/profiles                — 410 Gone（多 profile 已移除）
  PATCH  /api/profiles/{profile_id}   — 改名 / 改 description / 改 slug
  DELETE /api/profiles/{profile_id}   — 不能删默认（唯一 profile 即默认）→ 409

规则：
  - 所有端点要求 bearer auth。
  - 默认 profile.id == users.username；user_id == 十进制 users.id。
  - 非本人 profile 操作 → 404（避免泄露 profile_id 存在性）。
  - 删除默认 profile → 409。
  - 创建第二个 profile 的能力已移除：POST → 410。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# ``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
# dropped (commit 367e344). ``create_app`` leaves ``app.state.profile_store``
# unset unless ``session_store_url`` points at a reachable PG, so these
# profile HTTP tests require a real DB.
pytestmark = pytest.mark.requires_real_pg


def _make_client(tmp_path, users):
    """Return ``(app, client)`` wired to the real CRM PG.

    ``client`` is entered so lifespan populates ``app.state`` and each user's
    default profile is lazily created (via ``_resolve_profile_id`` on the
    warm-up request). Caller is responsible for closing it — use as a context
    manager (``with client:``)."""
    from hermes.crm.db import _resolve_dsn

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    app = create_app(
        AppConfig(
            api_keys={u.token: u.uid for u in users},
            internal_key="internal-secret",
            session_store_url=dsn,
            hermes_home_root=str(tmp_path / "hermes_home"),
        )
    )
    return app, TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/profiles
# ---------------------------------------------------------------------------

def test_list_profiles_returns_default_for_new_user(tmp_path, pg_users):
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        r = client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
    assert r.status_code == 200
    data = r.json()
    assert "profiles" in data
    assert len(data["profiles"]) == 1
    default = data["profiles"][0]
    assert default["is_default"] is True
    assert default["user_id"] == alice.uid  # 十进制 users.id
    assert default["id"] == alice.username  # 默认 profile id == username


def test_list_profiles_isolates_between_users(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app, client = _make_client(tmp_path, [alice, bob])
    with client:
        r_alice = client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        ).json()
        r_bob = client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {bob.token}"}
        ).json()
    # 1 user = 1 profile：各自只看到自己的默认 profile。
    assert [p["id"] for p in r_alice["profiles"]] == [alice.username]
    assert [p["id"] for p in r_bob["profiles"]] == [bob.username]


# ---------------------------------------------------------------------------
# POST /api/profiles — Charter v2: 已移除（410 Gone）
# ---------------------------------------------------------------------------

def test_create_profile_endpoint_is_gone(tmp_path, pg_users):
    """多 profile 创建能力已移除：端点返回 410 让任何残留调用方立刻暴露。"""
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        r = client.post(
            "/api/profiles",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"name": "research"},
        )
    assert r.status_code == 410


# ---------------------------------------------------------------------------
# PATCH /api/profiles/{id} — 唯一（默认）profile 可就地编辑
# ---------------------------------------------------------------------------

def test_rename_default_profile(tmp_path, pg_users):
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        # 懒建默认 profile
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
        r = client.patch(
            f"/api/profiles/{alice.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"name": "我的助手"},
        )
    assert r.status_code == 200
    assert r.json()["name"] == "我的助手"


def test_set_default_on_sole_profile_is_idempotent(tmp_path, pg_users):
    """1 user = 1 profile：在唯一 profile 上 set is_default 仍成功并保持默认。"""
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
        r = client.patch(
            f"/api/profiles/{alice.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"is_default": True},
        )
        assert r.status_code == 200
        listed = client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        ).json()["profiles"]
    defaults = [p for p in listed if p["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == alice.username


def test_patch_other_users_profile_returns_404(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app, client = _make_client(tmp_path, [alice, bob])
    with client:
        # 懒建 bob 的默认 profile
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {bob.token}"}
        )
        # alice 改 bob 的 profile → 404（不泄露存在性）
        r = client.patch(
            f"/api/profiles/{bob.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"name": "hijacked"},
        )
    assert r.status_code == 404


def test_patch_profile_updates_description(tmp_path, pg_users):
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
        r = client.patch(
            f"/api/profiles/{alice.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"description": "new desc"},
        )
    assert r.status_code == 200
    assert r.json()["description"] == "new desc"


def test_patch_profile_updates_slug(tmp_path, pg_users):
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
        r = client.patch(
            f"/api/profiles/{alice.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"slug": "renamed"},
        )
    assert r.status_code == 200
    assert r.json()["slug"] == "renamed"


def test_patch_profile_invalid_slug_returns_400(tmp_path, pg_users):
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
        r = client.patch(
            f"/api/profiles/{alice.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
            json={"slug": "Has Spaces!"},
        )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/profiles/{id}
# ---------------------------------------------------------------------------

def test_delete_default_profile_returns_409(tmp_path, pg_users):
    """用户的唯一 profile 即默认，不能删 → 409。"""
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
        r = client.delete(
            f"/api/profiles/{alice.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
        )
    assert r.status_code == 409


def test_delete_other_users_profile_returns_404(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app, client = _make_client(tmp_path, [alice, bob])
    with client:
        client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {bob.token}"}
        )
        r = client.delete(
            f"/api/profiles/{bob.username}",
            headers={"Authorization": f"Bearer {alice.token}"},
        )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# description + slug 默认值
# ---------------------------------------------------------------------------

def test_list_profiles_includes_description_and_slug(tmp_path, pg_users):
    (alice,) = pg_users(1)
    app, client = _make_client(tmp_path, [alice])
    with client:
        r = client.get(
            "/api/profiles", headers={"Authorization": f"Bearer {alice.token}"}
        )
    default = r.json()["profiles"][0]
    assert default["description"] == ""
    assert default["slug"] == "main"  # slugify(DEFAULT_PROFILE_NAME)
