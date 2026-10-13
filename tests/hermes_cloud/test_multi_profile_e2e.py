"""End-to-end multi-profile (Agent) 集成测试 (Task 9)。

这个文件把之前单独覆盖的各个层（profile_store / profile_manager / X-Profile-Id
路由 / CRUD API / warm-boot / auto-backfill）串成一条真实用户路径：

  1. 新用户首次访问 → 自动建默认 profile（``id == user_id``）
  2. 通过 ``POST /api/profiles`` 创建非默认 profile（= UI "新建 Agent"）
  3. 不同 profile 的 model / toolsets / prompt / agents 配置互不污染
  4. ``PATCH /api/profiles/{id}`` 改名、设为默认 都能透过 API 生效
  5. ``DELETE /api/profiles/{id}`` 软删除 + 默认不可删
  6. 跨用户严格隔离：Alice 带 Bob 的 profile_id → 404（不泄露存在性）
  7. 重启场景（SQLite 持久化）下 active profile 列表完整保留，供 warm-boot 消费

这里不再单独断言每个端点内部行为（那些单元 / contract 测试已经覆盖），
重点是 **整条链路** 的组合能否正确工作。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# ``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
# dropped (commit 367e344). This end-to-end multi-profile suite drives the
# CRUD/routing endpoints, all of which need ``app.state.profile_store`` —
# wired by ``create_app`` only when ``HERMES_CRM_DATABASE_URL`` reaches PG.
pytestmark = pytest.mark.requires_real_pg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app(tmp_path, *, users=None, db_url=None):
    users = users or {"sk-alice": "alice", "sk-bob": "bob"}
    # session_store_url 是单一 DB URL，被 auth / config / channel / session /
    # profile 全部 store 共用（见 main.py 里的 wiring）。
    cfg_kwargs = dict(api_keys=users, hermes_home_root=str(tmp_path))
    if db_url is not None:
        cfg_kwargs["session_store_url"] = db_url
    app = create_app(AppConfig(**cfg_kwargs))
    return app


def _auth(key: str, profile_id: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {key}"}
    if profile_id is not None:
        h["X-Profile-Id"] = profile_id
    return h


# ---------------------------------------------------------------------------
# 1. 全生命周期：一个用户，多个 Agent
# ---------------------------------------------------------------------------


def test_single_user_full_multi_profile_lifecycle(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)

    # --- step 1: 激活 lifespan + auto-backfill 默认 profile --------------------
    r = client.get("/api/profiles", headers=_auth("sk-alice"))
    assert r.status_code == 200
    profiles = r.json()["profiles"]
    assert len(profiles) == 1
    default = profiles[0]
    assert default["id"] == "alice"
    assert default["is_default"] is True

    # --- step 2: 创建两个非默认 profile -----------------------------------------
    r = client.post(
        "/api/profiles", headers=_auth("sk-alice"), json={"name": "research"}
    )
    assert r.status_code == 201
    research = r.json()
    assert research["id"] != "alice"
    assert research["is_default"] is False

    r = client.post(
        "/api/profiles", headers=_auth("sk-alice"), json={"name": "coding"}
    )
    assert r.status_code == 201
    coding = r.json()

    r = client.get("/api/profiles", headers=_auth("sk-alice"))
    ids = [p["id"] for p in r.json()["profiles"]]
    # 默认始终在最前
    assert ids[0] == "alice"
    assert set(ids) == {"alice", research["id"], coding["id"]}

    # --- step 3: 每个 profile 写不同 model / toolsets / prompt / agents ---------
    for label, pid in [
        ("default", None),
        ("research", research["id"]),
        ("coding", coding["id"]),
    ]:
        client.put(
            "/api/user-config/model",
            headers=_auth("sk-alice", pid),
            json={
                "model": f"{label}-model",
                "api_key": f"{label}-key",
                "base_url": f"https://{label}.example",
            },
        )
        client.put(
            "/api/user-config/toolsets",
            headers=_auth("sk-alice", pid),
            json={"toolsets": [f"{label}-tool"]},
        )
        client.post(
            "/api/user-config/agents",
            headers=_auth("sk-alice", pid),
            json={"system_prompt": f"{label}-sys", "memory_enabled": True},
        )

    def _assert_profile_sees(label: str, pid: str | None):
        got_model = client.get(
            "/api/user-config/model", headers=_auth("sk-alice", pid)
        ).json()
        assert got_model["model"] == f"{label}-model"
        got_tool = client.get(
            "/api/user-config/toolsets", headers=_auth("sk-alice", pid)
        ).json()
        assert got_tool["toolsets"] == [f"{label}-tool"]
        got_agent = client.get(
            "/api/user-config/agents", headers=_auth("sk-alice", pid)
        ).json()
        assert got_agent["system_prompt"] == f"{label}-sys"

    _assert_profile_sees("default", None)
    _assert_profile_sees("research", research["id"])
    _assert_profile_sees("coding", coding["id"])

    # --- step 4a: 改名 ---------------------------------------------------------
    r = client.patch(
        f"/api/profiles/{research['id']}",
        headers=_auth("sk-alice"),
        json={"name": "research-v2"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "research-v2"

    # --- step 4b: 把 research 设为默认，旧默认降级 --------------------------------
    r = client.patch(
        f"/api/profiles/{research['id']}",
        headers=_auth("sk-alice"),
        json={"is_default": True},
    )
    assert r.status_code == 200
    assert r.json()["is_default"] is True

    profiles = client.get("/api/profiles", headers=_auth("sk-alice")).json()[
        "profiles"
    ]
    defaults = [p for p in profiles if p["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == research["id"]
    # 数据没搞丢：旧默认 id==alice 仍在列表里，只是不再是默认
    assert any(p["id"] == "alice" and not p["is_default"] for p in profiles)

    # 现在不带 X-Profile-Id 访问 → 打到新默认（research）
    got = client.get(
        "/api/user-config/model", headers=_auth("sk-alice")
    ).json()
    assert got["model"] == "research-model"

    # --- step 5: 删除默认应该失败 ----------------------------------------------
    r = client.delete(
        f"/api/profiles/{research['id']}", headers=_auth("sk-alice")
    )
    assert r.status_code == 409

    # --- step 6: 删除非默认 (coding) 成功 ---------------------------------------
    r = client.delete(
        f"/api/profiles/{coding['id']}", headers=_auth("sk-alice")
    )
    assert r.status_code == 204
    profiles = client.get("/api/profiles", headers=_auth("sk-alice")).json()[
        "profiles"
    ]
    assert all(p["id"] != coding["id"] for p in profiles)

    # 删除后再带着它的 profile_id 访问 → 403/404（不再是 active）
    r = client.get(
        "/api/user-config/model",
        headers=_auth("sk-alice", coding["id"]),
    )
    assert r.status_code in (403, 404)


# ---------------------------------------------------------------------------
# 2. 跨用户隔离
# ---------------------------------------------------------------------------


def test_cross_user_profile_isolation_e2e(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)

    # 激活 + 建默认
    client.get("/api/profiles", headers=_auth("sk-alice"))
    client.get("/api/profiles", headers=_auth("sk-bob"))

    # Bob 建一个 profile
    r = client.post(
        "/api/profiles", headers=_auth("sk-bob"), json={"name": "bob-work"}
    )
    assert r.status_code == 201
    bob_work_id = r.json()["id"]

    # Alice 的 list 里看不到 Bob 的 profile
    alice_ids = {
        p["id"]
        for p in client.get("/api/profiles", headers=_auth("sk-alice")).json()[
            "profiles"
        ]
    }
    assert bob_work_id not in alice_ids
    assert "alice" in alice_ids
    assert "bob" not in alice_ids

    # Alice 用 bob_work_id 访问 config → 403（X-Profile-Id 归属权校验）
    r = client.get(
        "/api/user-config/model",
        headers=_auth("sk-alice", bob_work_id),
    )
    assert r.status_code == 403

    # Alice 用 bob_work_id 改名 → 404
    r = client.patch(
        f"/api/profiles/{bob_work_id}",
        headers=_auth("sk-alice"),
        json={"name": "hacked"},
    )
    assert r.status_code == 404

    # Alice 用 bob_work_id 删除 → 404
    r = client.delete(
        f"/api/profiles/{bob_work_id}", headers=_auth("sk-alice")
    )
    assert r.status_code == 404

    # Bob 本人仍能正常读 / 写
    r = client.put(
        "/api/user-config/model",
        headers=_auth("sk-bob", bob_work_id),
        json={"model": "bob-m", "api_key": "k", "base_url": "https://b"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 3. 重启场景：profile 数据持久化 + warm-boot 列表完整
# ---------------------------------------------------------------------------


def test_profiles_persist_across_app_restart_and_warm_boot_sees_all(tmp_path):
    db_path = tmp_path / "profiles.db"
    db_url = f"sqlite+pysqlite:///{db_path}"

    # -------- 第一次启动：建三个用户的若干 profile ------------------------------
    app1 = _make_app(
        tmp_path,
        users={"sk-alice": "alice", "sk-bob": "bob", "sk-carol": "carol"},
        db_url=db_url,
    )
    with TestClient(app1) as c1:
        c1.get("/api/profiles", headers=_auth("sk-alice"))
        c1.get("/api/profiles", headers=_auth("sk-bob"))
        c1.get("/api/profiles", headers=_auth("sk-carol"))
        r = c1.post(
            "/api/profiles",
            headers=_auth("sk-alice"),
            json={"name": "research"},
        )
        assert r.status_code == 201
        alice_research = r.json()["id"]
        r = c1.post(
            "/api/profiles",
            headers=_auth("sk-bob"),
            json={"name": "sales"},
        )
        assert r.status_code == 201
        bob_sales = r.json()["id"]

    # -------- 第二次启动：同一个 DB URL，全部 profile 应该还在 -------------------
    app2 = _make_app(
        tmp_path,
        users={"sk-alice": "alice", "sk-bob": "bob", "sk-carol": "carol"},
        db_url=db_url,
    )
    # warm-boot 路径本身读的是 list_all_active_profiles —— 直接验证这个接口
    # 返回了所有预期 profile（包括非默认）
    active = app2.state.profile_store.list_all_active_profiles()
    ids = {p.id for p in active}
    assert {"alice", "bob", "carol", alice_research, bob_sales} <= ids

    # 用 HTTP 层再读一次：每个用户看到自己的全部 profile
    with TestClient(app2) as c2:
        alice_list = c2.get(
            "/api/profiles", headers=_auth("sk-alice")
        ).json()["profiles"]
        assert {p["id"] for p in alice_list} == {"alice", alice_research}

        bob_list = c2.get("/api/profiles", headers=_auth("sk-bob")).json()[
            "profiles"
        ]
        assert {p["id"] for p in bob_list} == {"bob", bob_sales}

        carol_list = c2.get(
            "/api/profiles", headers=_auth("sk-carol")
        ).json()["profiles"]
        assert {p["id"] for p in carol_list} == {"carol"}


# ---------------------------------------------------------------------------
# 4. ensure_default_profile 在 lifespan auto-backfill 之后仍然 idempotent
# ---------------------------------------------------------------------------


def test_auto_backfill_is_idempotent_across_requests(tmp_path):
    app = _make_app(tmp_path, users={"sk-alice": "alice"})
    client = TestClient(app)

    # 多次发请求：每次 _resolve_profile_id 都会走一次 ensure_default_profile，
    # 不应该产生额外的默认 row。
    for _ in range(5):
        client.get("/api/profiles", headers=_auth("sk-alice"))
        client.get(
            "/api/user-config/model", headers=_auth("sk-alice")
        )

    profiles = client.get("/api/profiles", headers=_auth("sk-alice")).json()[
        "profiles"
    ]
    assert len(profiles) == 1
    assert profiles[0]["is_default"] is True
    assert profiles[0]["id"] == "alice"
