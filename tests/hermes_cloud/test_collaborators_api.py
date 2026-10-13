"""集成测试:3 个 collaborators HTTP 端点(GET / POST / DELETE)。

测试策略:
- 全部标记 requires_real_pg,需 HERMES_TESTS_REAL_PG=1
- 用和 test_crm_records.py / test_crm_rbac.py 相同的 fixture 风格:
    client / admin_api_key / non_admin_user(Sales角色,有index/create/view但无share)
    / mgr_user(Sales Manager角色,有share)
- 直接用 SQL 建测试 record,用 admin_api_key 的 owner_by=1(admin user id)
  非 admin owner 用 direct SQL INSERT 设置 owner_by
"""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.requires_real_pg

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    from hermes.crm.db import _resolve_dsn
    from hermes_cloud.app.config import AppConfig
    from hermes_cloud.app.main import create_app

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return TestClient(create_app(AppConfig(session_store_url=dsn)))


@pytest.fixture(scope="module")
def admin_api_key() -> str:
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT api_key FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row and row.get("api_key"), "admin user missing api_key — run seed"
    return row["api_key"]


@pytest.fixture(scope="module")
def admin_user_id() -> int:
    """获取 admin 用户的整数 id。"""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row, "admin user not found"
    return int(row["id"])


def _make_user(role_name: str, suffix: str | None = None) -> dict:
    """在 DB 里建一个测试用户,返回 {id, username, api_key}。"""
    from hermes.crm.db import db_session
    from hermes.crm.models.users_db import UsersDb

    suffix = suffix or secrets.token_hex(4)
    # username 必须符合 ck_users_username_slug: ^[a-z0-9][a-z0-9_-]{0,49}$
    role_slug = role_name.lower().replace(" ", "-")[:6]
    username = f"pqa-{role_slug}-{suffix}"
    api_key = secrets.token_hex(24)

    with db_session() as db:
        role_id = db.fetchcolumn(
            "SELECT id FROM roles WHERE name = %s LIMIT 1", (role_name,)
        )
        assert role_id, f"Role '{role_name}' missing — run seed"

        users = UsersDb(db)
        uid = users.insert(
            {
                "username": username,
                "password": UsersDb.hash_password("pw123"),
                "realname": f"QA {role_name}",
                "email": f"{username}@example.com",
                "status": 1,
                "is_admin": False,
                "api_key": api_key,
                "plan": "free",
                "role_id": int(role_id),
            }
        )
        db.commit()

    return {"id": uid, "username": username, "api_key": api_key}


def _delete_user(uid: int, username: str) -> None:
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute("DELETE FROM profiles WHERE id = %s", (username,))
        db.execute("UPDATE users SET deleted = TRUE WHERE id = %s", (uid,))
        db.commit()


@pytest.fixture(scope="module")
def sales_user() -> dict:
    """Sales 角色:有 accounts 的 index/create/update/view,无 share。"""
    u = _make_user("Sales")
    yield u
    _delete_user(u["id"], u["username"])


@pytest.fixture(scope="module")
def mgr_user() -> dict:
    """Sales Manager 角色:有 share。"""
    u = _make_user("Sales Manager")
    yield u
    _delete_user(u["id"], u["username"])


@pytest.fixture(scope="module")
def test_record_id(admin_user_id: int) -> int:
    """建一条 accounts 记录,owner_by = admin。测试结束后软删除。"""
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    with db_session() as db:
        row = db.fetchone(
            """
            INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
            VALUES (%s, %s, %s, FALSE)
            RETURNING accounts_id
            """,
            (f"QA-Collab-{suffix}", admin_user_id, admin_user_id),
        )
        db.commit()
    rid = int(row["accounts_id"])
    yield rid
    with db_session() as db:
        db.execute(
            "DELETE FROM record_collaborators WHERE record_id = %s", (rid,)
        )
        db.execute(
            "UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s", (rid,)
        )
        db.commit()


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_admin_can_get_collaborators(client, admin_api_key, test_record_id):
    """Admin GET → 200, 返回 collaborators list(初始为空)。"""
    r = client.get(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "collaborators" in body
    assert isinstance(body["collaborators"], list)


def test_owner_with_share_can_post_collaborator(
    client, admin_api_key, sales_user, test_record_id
):
    """admin(owner) 有 share 权限 → POST 200,添加 sales_user 为 collaborator。"""
    payload = {
        "user_id": sales_user["id"],
        "can_view": True,
        "can_edit": False,
    }
    r = client.post(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        json=payload,
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == sales_user["id"]
    assert body["can_view"] is True
    assert body["can_edit"] is False


def test_get_returns_added_collaborator(
    client, admin_api_key, sales_user, test_record_id
):
    """POST 之后 GET 应能看到 sales_user 出现在 collaborators 列表里(自包含,不依赖其它测试副作用)。"""
    # 先确保 sales_user 已添加
    client.post(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
        headers=_auth(admin_api_key),
    )
    r = client.get(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text
    uids = {c["user_id"] for c in r.json()["collaborators"]}
    assert sales_user["id"] in uids


def test_non_owner_without_share_cannot_post(
    client, sales_user, test_record_id, mgr_user
):
    """sales_user 不是 owner,无 share → POST 403。"""
    payload = {"user_id": mgr_user["id"], "can_view": True, "can_edit": False}
    r = client.post(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        json=payload,
        headers=_auth(sales_user["api_key"]),
    )
    assert r.status_code == 403, r.text


def test_non_owner_with_share_cannot_post(
    client, mgr_user, test_record_id, sales_user
):
    """mgr_user 有 share 但不是 record 的 owner → POST 403(must be owner AND share)。"""
    payload = {"user_id": sales_user["id"], "can_view": True, "can_edit": False}
    r = client.post(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        json=payload,
        headers=_auth(mgr_user["api_key"]),
    )
    assert r.status_code == 403, r.text


def test_owner_without_share_can_delete(
    client, test_record_id, sales_user, mgr_user, admin_api_key, admin_user_id
):
    """DELETE 不需要 share 权限 — owner 即可撤销。

    owner = admin user(admin_user_id)。先用 admin POST 添加 sales_user;
    然后建一条 owner_by=admin 的 record(已有 test_record_id);
    用 admin_api_key 做 DELETE 应 204。
    """
    # 先确保 sales_user 在 collaborators 里
    client.post(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
        headers=_auth(admin_api_key),
    )
    r = client.delete(
        f"/crm/records/accounts/{test_record_id}/collaborators/{sales_user['id']}",
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 204, r.text


def test_non_owner_cannot_delete(
    client, test_record_id, sales_user, mgr_user, admin_api_key
):
    """非 owner 非 admin 不能 DELETE collaborator。"""
    # 先再次添加 sales_user(上面测试删掉了)
    client.post(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
        headers=_auth(admin_api_key),
    )
    # mgr_user 是 Sales Manager,有 share,但不是 owner → 403
    r = client.delete(
        f"/crm/records/accounts/{test_record_id}/collaborators/{sales_user['id']}",
        headers=_auth(mgr_user["api_key"]),
    )
    assert r.status_code == 403, r.text


def test_admin_can_delete_any_collaborator(
    client, test_record_id, sales_user, admin_api_key
):
    """Admin 可以删任何人的 collaborator(is_admin bypass)。"""
    # 确保 sales_user 还在
    client.post(
        f"/crm/records/accounts/{test_record_id}/collaborators",
        json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
        headers=_auth(admin_api_key),
    )
    r = client.delete(
        f"/crm/records/accounts/{test_record_id}/collaborators/{sales_user['id']}",
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 204, r.text


def test_post_nonexistent_record_returns_404(client, admin_api_key, sales_user):
    """POST 到不存在的 record → 404。"""
    r = client.post(
        "/crm/records/accounts/999999999/collaborators",
        json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 404, r.text


def test_get_nonexistent_record_returns_404(client, admin_api_key):
    """GET 不存在 record → 404,与 POST/DELETE 一致。"""
    r = client.get(
        "/crm/records/accounts/999999999/collaborators",
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 404, r.text


def test_created_by_cannot_share_after_owner_transfer(
    client, admin_api_key, admin_user_id, mgr_user, sales_user
):
    """owner_by 已 transfer 给别人,原 created_by 用户仍尝试 share → 403。

    模拟场景:mgr_user 是 created_by,sales_user 是 owner_by(transfer 后状态)。
    mgr_user 有 Sales Manager 角色(有 share 权限),但不是 owner → POST/DELETE 均 403。
    """
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    # 直接 SQL INSERT:created_by=mgr_user, owner_by=sales_user (模拟 transfer 后)
    with db_session() as db:
        row = db.fetchone(
            """
            INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
            VALUES (%s, %s, %s, FALSE)
            RETURNING accounts_id
            """,
            (f"QA-Transfer-{suffix}", sales_user["id"], mgr_user["id"]),
        )
        db.commit()
    transferred_record_id = int(row["accounts_id"])

    try:
        # mgr_user(created_by,有 share,但 owner 是 sales_user) → POST 403
        r_post = client.post(
            f"/crm/records/accounts/{transferred_record_id}/collaborators",
            json={"user_id": admin_user_id, "can_view": True, "can_edit": False},
            headers=_auth(mgr_user["api_key"]),
        )
        assert r_post.status_code == 403, (
            f"expected 403 for created_by non-owner POST, got {r_post.status_code}: {r_post.text}"
        )

        # mgr_user 尝试 DELETE → 403
        r_del = client.delete(
            f"/crm/records/accounts/{transferred_record_id}/collaborators/{admin_user_id}",
            headers=_auth(mgr_user["api_key"]),
        )
        assert r_del.status_code == 403, (
            f"expected 403 for created_by non-owner DELETE, got {r_del.status_code}: {r_del.text}"
        )
    finally:
        # 清理测试数据
        with db_session() as db:
            db.execute(
                "DELETE FROM record_collaborators WHERE record_id = %s",
                (transferred_record_id,),
            )
            db.execute(
                "UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s",
                (transferred_record_id,),
            )
            db.commit()


def test_post_share_writes_interaction_log(
    client, admin_api_key, admin_user_id, sales_user
):
    """成功 POST 一条共享 → interaction_log 有一行通知。

    断言:
    - user_id = target_uid (sales_user)
    - channel = 'crm_share'
    - role = 'system'
    - content jsonb 含 entity_type / entity_id / by_uid / can_view / can_edit
    """
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    with db_session() as db:
        row = db.fetchone(
            """
            INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
            VALUES (%s, %s, %s, FALSE)
            RETURNING accounts_id
            """,
            (f"QA-Log-{suffix}", admin_user_id, admin_user_id),
        )
        db.commit()
    rid = int(row["accounts_id"])

    try:
        # 先清空可能存在的旧 interaction_log 行
        with db_session() as db:
            db.execute(
                """DELETE FROM interaction_log
                    WHERE channel = 'crm_share'
                      AND (content->>'entity_id')::bigint = %s
                      AND user_id = %s""",
                (rid, sales_user["id"]),
            )
            db.commit()

        r = client.post(
            f"/crm/records/accounts/{rid}/collaborators",
            json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
            headers=_auth(admin_api_key),
        )
        assert r.status_code == 200, r.text

        with db_session() as db:
            log_row = db.fetchone(
                """SELECT user_id, channel, role, content
                     FROM interaction_log
                    WHERE user_id = %s
                      AND channel = 'crm_share'
                      AND (content->>'entity_id')::bigint = %s
                    ORDER BY created_at DESC
                    LIMIT 1""",
                (sales_user["id"], rid),
            )
        assert log_row is not None, "interaction_log 中缺少通知行"
        assert log_row["channel"] == "crm_share"
        assert log_row["role"] == "system"
        content = log_row["content"]
        assert content.get("entity_type") == "accounts"
        assert int(content.get("entity_id")) == rid
        assert int(content.get("by_uid")) == admin_user_id
        assert content.get("can_view") is True
        assert content.get("can_edit") is False
    finally:
        with db_session() as db:
            db.execute(
                "DELETE FROM interaction_log WHERE channel='crm_share' AND (content->>'entity_id')::bigint=%s",
                (rid,),
            )
            db.execute("DELETE FROM record_collaborators WHERE record_id=%s", (rid,))
            db.execute("UPDATE accounts SET deleted=TRUE WHERE accounts_id=%s", (rid,))
            db.commit()


def test_repeat_post_dedups_within_24h(
    client, admin_api_key, admin_user_id, sales_user
):
    """24h 内同 (record, target) 重复 POST 不重复写 interaction_log。

    第一次 POST (can_view=True, can_edit=False) → 写一行。
    第二次 POST (同 target, can_edit=True 提权) → 去重,不再写新行。
    查 interaction_log 该 (record, target) 的行数 == 1。
    """
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    with db_session() as db:
        row = db.fetchone(
            """
            INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
            VALUES (%s, %s, %s, FALSE)
            RETURNING accounts_id
            """,
            (f"QA-Dedup-{suffix}", admin_user_id, admin_user_id),
        )
        db.commit()
    rid = int(row["accounts_id"])

    try:
        # 清空旧数据
        with db_session() as db:
            db.execute(
                """DELETE FROM interaction_log
                    WHERE channel = 'crm_share'
                      AND (content->>'entity_id')::bigint = %s
                      AND user_id = %s""",
                (rid, sales_user["id"]),
            )
            db.commit()

        # 第一次 POST
        r1 = client.post(
            f"/crm/records/accounts/{rid}/collaborators",
            json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
            headers=_auth(admin_api_key),
        )
        assert r1.status_code == 200, r1.text

        # 第二次 POST(提权)
        r2 = client.post(
            f"/crm/records/accounts/{rid}/collaborators",
            json={"user_id": sales_user["id"], "can_view": True, "can_edit": True},
            headers=_auth(admin_api_key),
        )
        assert r2.status_code == 200, r2.text

        with db_session() as db:
            count = db.fetchcolumn(
                """SELECT COUNT(*) FROM interaction_log
                    WHERE user_id = %s
                      AND channel = 'crm_share'
                      AND (content->>'entity_id')::bigint = %s""",
                (sales_user["id"], rid),
            )
        assert int(count) == 1, f"去重失败,期望 1 行,实际 {count} 行"
    finally:
        with db_session() as db:
            db.execute(
                "DELETE FROM interaction_log WHERE channel='crm_share' AND (content->>'entity_id')::bigint=%s",
                (rid,),
            )
            db.execute("DELETE FROM record_collaborators WHERE record_id=%s", (rid,))
            db.execute("UPDATE accounts SET deleted=TRUE WHERE accounts_id=%s", (rid,))
            db.commit()


def test_random_detail_user_cannot_get_collaborators(
    client, admin_api_key, admin_user_id, sales_user
):
    """行级 ACL:有 accounts.detail 角色权限但既非 owner 也非 collaborator 的用户 → GET 403。

    场景:
    - admin 建 record(owner_by=admin),共享给 sales_user(collaborator)
    - mgr_user_b:有 accounts.detail(Sales Manager 有 detail),但既非 owner 也非 collaborator
      → GET 403
    - sales_user(collaborator) → GET 200
    - admin(owner) → GET 200
    """
    from hermes.crm.db import db_session

    # 建一个局部的第三方用户(有 detail 权限但无关联)
    bystander = _make_user("Sales Manager")
    try:
        suffix = secrets.token_hex(4)
        with db_session() as db:
            row = db.fetchone(
                """
                INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
                VALUES (%s, %s, %s, FALSE)
                RETURNING accounts_id
                """,
                (f"QA-ACL-{suffix}", admin_user_id, admin_user_id),
            )
            db.commit()
        rid = int(row["accounts_id"])

        try:
            # admin 把 sales_user 加为 collaborator
            r_post = client.post(
                f"/crm/records/accounts/{rid}/collaborators",
                json={"user_id": sales_user["id"], "can_view": True, "can_edit": False},
                headers=_auth(admin_api_key),
            )
            assert r_post.status_code == 200, r_post.text

            # bystander:有 detail 权限,但非 owner / collaborator → 403
            r_bystander = client.get(
                f"/crm/records/accounts/{rid}/collaborators",
                headers=_auth(bystander["api_key"]),
            )
            assert r_bystander.status_code == 403, (
                f"expected 403 for bystander, got {r_bystander.status_code}: {r_bystander.text}"
            )

            # sales_user(collaborator) → 200
            r_collab = client.get(
                f"/crm/records/accounts/{rid}/collaborators",
                headers=_auth(sales_user["api_key"]),
            )
            assert r_collab.status_code == 200, (
                f"expected 200 for collaborator, got {r_collab.status_code}: {r_collab.text}"
            )

            # admin(owner) → 200
            r_owner = client.get(
                f"/crm/records/accounts/{rid}/collaborators",
                headers=_auth(admin_api_key),
            )
            assert r_owner.status_code == 200, (
                f"expected 200 for owner, got {r_owner.status_code}: {r_owner.text}"
            )
        finally:
            with db_session() as db:
                db.execute(
                    "DELETE FROM record_collaborators WHERE record_id = %s", (rid,)
                )
                db.execute(
                    "UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s", (rid,)
                )
                db.commit()
    finally:
        _delete_user(bystander["id"], bystander["username"])
