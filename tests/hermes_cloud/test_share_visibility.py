"""被共享后的可见性测试:detail 路由接通 collaborator path。

测试策略:
- 标记 requires_real_pg,需 HERMES_TESTS_REAL_PG=1
- fixture 风格照 test_collaborators_api.py:client / admin_api_key / _make_user / SQL 建 record
- 端点前缀 /crm(不是 /api/crm)
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


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_non_owner_collaborator_can_get_detail(client, admin_api_key, admin_user_id):
    """被共享 can_view=true 后能 GET 详情(原本会 404)。"""
    from hermes.crm.db import db_session

    # 建 record,owner_by = admin(id=admin_user_id),不是 other_user 的上下属
    other_user = _make_user("Sales")
    try:
        suffix = secrets.token_hex(4)
        with db_session() as db:
            row = db.fetchone(
                """
                INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
                VALUES (%s, %s, %s, FALSE)
                RETURNING accounts_id
                """,
                (f"QA-Vis-{suffix}", admin_user_id, admin_user_id),
            )
            db.commit()
        rid = int(row["accounts_id"])

        try:
            # 验证 other_user 初始拿不到(404)
            r_before = client.get(
                f"/crm/records/accounts/{rid}",
                headers=_auth(other_user["api_key"]),
            )
            assert r_before.status_code == 404, (
                f"expected 404 before share, got {r_before.status_code}: {r_before.text}"
            )

            # admin 把 other_user 添加为 collaborator(can_view=True)
            r_share = client.post(
                f"/crm/records/accounts/{rid}/collaborators",
                json={"user_id": other_user["id"], "can_view": True, "can_edit": False},
                headers=_auth(admin_api_key),
            )
            assert r_share.status_code == 200, (
                f"POST collaborator failed: {r_share.status_code}: {r_share.text}"
            )

            # 现在 other_user 应该能看到
            r_after = client.get(
                f"/crm/records/accounts/{rid}",
                headers=_auth(other_user["api_key"]),
            )
            assert r_after.status_code == 200, (
                f"expected 200 after share, got {r_after.status_code}: {r_after.text}"
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
        _delete_user(other_user["id"], other_user["username"])


def test_non_owner_non_collaborator_still_404(client, admin_user_id):
    """没被共享、非 owner、非下属 → 404 不变。"""
    from hermes.crm.db import db_session

    other_user = _make_user("Sales")
    try:
        suffix = secrets.token_hex(4)
        with db_session() as db:
            row = db.fetchone(
                """
                INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
                VALUES (%s, %s, %s, FALSE)
                RETURNING accounts_id
                """,
                (f"QA-No-Share-{suffix}", admin_user_id, admin_user_id),
            )
            db.commit()
        rid = int(row["accounts_id"])

        try:
            r = client.get(
                f"/crm/records/accounts/{rid}",
                headers=_auth(other_user["api_key"]),
            )
            assert r.status_code == 404, (
                f"expected 404 for non-collaborator, got {r.status_code}: {r.text}"
            )
        finally:
            with db_session() as db:
                db.execute(
                    "UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s", (rid,)
                )
                db.commit()
    finally:
        _delete_user(other_user["id"], other_user["username"])


# ---------------------------------------------------------------------------
# Task 5: list 路由 — 默认 scope 合并被共享 record
# ---------------------------------------------------------------------------


def _setup_shared_record(admin_user_id: int, other_user: dict, admin_api_key: str, client) -> int:
    """建一条 owner_by=admin 的 account,共享给 other_user(can_view=True)。返回 rid。"""
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    with db_session() as db:
        row = db.fetchone(
            """
            INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
            VALUES (%s, %s, %s, FALSE)
            RETURNING accounts_id
            """,
            (f"QA-ListVis-{suffix}", admin_user_id, admin_user_id),
        )
        db.commit()
    rid = int(row["accounts_id"])

    r_share = client.post(
        f"/crm/records/accounts/{rid}/collaborators",
        json={"user_id": other_user["id"], "can_view": True, "can_edit": False},
        headers=_auth(admin_api_key),
    )
    assert r_share.status_code == 200, (
        f"POST collaborator failed: {r_share.status_code}: {r_share.text}"
    )
    return rid


def _cleanup_record(rid: int) -> None:
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute("DELETE FROM record_collaborators WHERE record_id = %s", (rid,))
        db.execute("UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s", (rid,))
        db.commit()


def _record_ids_in_response(resp_json: dict) -> set[int]:
    """从 list 响应里提取所有 record id(兼容 data/items/rows 三种 key)。"""
    data = resp_json.get("data") or resp_json.get("items") or resp_json.get("rows") or []
    ids: set[int] = set()
    for item in data:
        if "accounts_id" in item:
            ids.add(int(item["accounts_id"]))
        elif "id" in item:
            ids.add(int(item["id"]))
    return ids


def test_shared_record_appears_in_default_list(client, admin_api_key, admin_user_id):
    """被共享 can_view=true → 默认 list(无 scope 参数)应包含该 record。"""
    other_user = _make_user("Sales")
    try:
        rid = _setup_shared_record(admin_user_id, other_user, admin_api_key, client)
        try:
            # 默认 scope(无 ?scope=),期望看到 rid
            r = client.get(
                "/crm/records/accounts?limit=200",
                headers=_auth(other_user["api_key"]),
            )
            assert r.status_code == 200, (
                f"list failed: {r.status_code}: {r.text}"
            )
            ids = _record_ids_in_response(r.json())
            assert rid in ids, (
                f"shared record {rid} not in default list; ids={sorted(ids)[:20]}"
            )
        finally:
            _cleanup_record(rid)
    finally:
        _delete_user(other_user["id"], other_user["username"])


def test_shared_record_NOT_in_scope_self(client, admin_api_key, admin_user_id):
    """scope=self 时共享 record(owner_by≠self)不应出现在结果里。"""
    other_user = _make_user("Sales")
    try:
        rid = _setup_shared_record(admin_user_id, other_user, admin_api_key, client)
        try:
            r = client.get(
                "/crm/records/accounts?scope=self&limit=200",
                headers=_auth(other_user["api_key"]),
            )
            assert r.status_code == 200, (
                f"list(scope=self) failed: {r.status_code}: {r.text}"
            )
            ids = _record_ids_in_response(r.json())
            assert rid not in ids, (
                f"shared record {rid} should NOT be in scope=self list, but it appeared"
            )
        finally:
            _cleanup_record(rid)
    finally:
        _delete_user(other_user["id"], other_user["username"])


def test_shared_record_NOT_in_scope_team(client, admin_api_key, admin_user_id):
    """scope=team 时共享 record 不应因共享而混入(owner 不在 other_user 层级)。"""
    other_user = _make_user("Sales")
    try:
        rid = _setup_shared_record(admin_user_id, other_user, admin_api_key, client)
        try:
            r = client.get(
                "/crm/records/accounts?scope=team&limit=200",
                headers=_auth(other_user["api_key"]),
            )
            assert r.status_code == 200, (
                f"list(scope=team) failed: {r.status_code}: {r.text}"
            )
            ids = _record_ids_in_response(r.json())
            assert rid not in ids, (
                f"shared record {rid} should NOT be in scope=team list, but it appeared"
            )
        finally:
            _cleanup_record(rid)
    finally:
        _delete_user(other_user["id"], other_user["username"])


# ---------------------------------------------------------------------------
# Task 6: update gate — 双达语义
# ---------------------------------------------------------------------------


def _make_role_no_update(suffix: str) -> int:
    """创建一个只有 index+view+create(无 update)的临时 role,返回 role_id。"""
    from hermes.crm.db import db_session

    role_name = f"qa-no-upd-{suffix}"
    with db_session() as db:
        # 获取 accounts module id
        mod = db.fetchone("SELECT id FROM modules WHERE table_name = 'accounts' LIMIT 1")
        assert mod, "accounts module not found"
        module_id = int(mod["id"])

        # 获取 index/view/create 的 permission id
        perms = db.fetchall(
            "SELECT id, name FROM permissions WHERE name IN ('index', 'view', 'create')"
        )
        perm_ids = [int(p["id"]) for p in perms]
        assert perm_ids, "base permissions not found"

        # 插入 role
        role_row = db.fetchone(
            "INSERT INTO roles (name) VALUES (%s) RETURNING id",
            (role_name,),
        )
        role_id = int(role_row["id"])

        # 为 accounts 模块分配 index+view+create
        for pid in perm_ids:
            db.execute(
                """
                INSERT INTO role_permissions (role_id, permission_id, module_id, has_permission)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT DO NOTHING
                """,
                (role_id, pid, module_id),
            )
        db.commit()
    return role_id


def _delete_role(role_id: int) -> None:
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute("DELETE FROM role_permissions WHERE role_id = %s", (role_id,))
        db.execute("DELETE FROM roles WHERE id = %s", (role_id,))
        db.commit()


def _make_user_with_role_id(role_id: int, suffix: str) -> dict:
    """在 DB 里建一个测试用户,绑定指定 role_id,返回 {id, username, api_key}。"""
    from hermes.crm.db import db_session
    from hermes.crm.models.users_db import UsersDb

    username = f"pqa-noupd-{suffix}"
    api_key = secrets.token_hex(24)

    with db_session() as db:
        users = UsersDb(db)
        uid = users.insert(
            {
                "username": username,
                "password": UsersDb.hash_password("pw123"),
                "realname": "QA NoUpdate",
                "email": f"{username}@example.com",
                "status": 1,
                "is_admin": False,
                "api_key": api_key,
                "plan": "free",
                "role_id": role_id,
            }
        )
        db.commit()

    return {"id": uid, "username": username, "api_key": api_key}


def test_collaborator_with_can_edit_and_role_update_can_modify(
    client, admin_api_key, admin_user_id
):
    """双达:can_edit=true + 角色有 accounts.update → PUT 200。"""
    from hermes.crm.db import db_session

    # Sales Manager 角色有 update 权限
    editor = _make_user("Sales Manager")
    try:
        suffix = secrets.token_hex(4)
        with db_session() as db:
            row = db.fetchone(
                """
                INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
                VALUES (%s, %s, %s, FALSE)
                RETURNING accounts_id
                """,
                (f"QA-Edit-{suffix}", admin_user_id, admin_user_id),
            )
            db.commit()
        rid = int(row["accounts_id"])

        try:
            # 共享 can_edit=True
            r_share = client.post(
                f"/crm/records/accounts/{rid}/collaborators",
                json={"user_id": editor["id"], "can_view": True, "can_edit": True},
                headers=_auth(admin_api_key),
            )
            assert r_share.status_code == 200, (
                f"POST collaborator failed: {r_share.status_code}: {r_share.text}"
            )

            # editor 发 PUT(payload 直接是字段 dict)
            r_put = client.put(
                f"/crm/records/accounts/{rid}",
                json={"accounts_name": f"edited-{suffix}"},
                headers=_auth(editor["api_key"]),
            )
            assert r_put.status_code == 200, (
                f"expected 200 (collaborator can_edit+role.update), "
                f"got {r_put.status_code}: {r_put.text}"
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
        _delete_user(editor["id"], editor["username"])


def test_collaborator_can_edit_but_no_role_update_403(
    client, admin_api_key, admin_user_id
):
    """双达不满足:can_edit=true 但角色无 accounts.update → PUT 403。"""
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    role_id = _make_role_no_update(suffix)
    try:
        no_update_user = _make_user_with_role_id(role_id, suffix)
        try:
            with db_session() as db:
                row = db.fetchone(
                    """
                    INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
                    VALUES (%s, %s, %s, FALSE)
                    RETURNING accounts_id
                    """,
                    (f"QA-NoUpd-{suffix}", admin_user_id, admin_user_id),
                )
                db.commit()
            rid = int(row["accounts_id"])

            try:
                # 共享 can_edit=True
                r_share = client.post(
                    f"/crm/records/accounts/{rid}/collaborators",
                    json={
                        "user_id": no_update_user["id"],
                        "can_view": True,
                        "can_edit": True,
                    },
                    headers=_auth(admin_api_key),
                )
                assert r_share.status_code == 200, (
                    f"POST collaborator failed: {r_share.status_code}: {r_share.text}"
                )

                # no_update_user 发 PUT → role 无 update → 403
                r_put = client.put(
                    f"/crm/records/accounts/{rid}",
                    json={"accounts_name": f"should-fail-{suffix}"},
                    headers=_auth(no_update_user["api_key"]),
                )
                assert r_put.status_code == 403, (
                    f"expected 403 (no role.update), "
                    f"got {r_put.status_code}: {r_put.text}"
                )
            finally:
                with db_session() as db:
                    db.execute(
                        "DELETE FROM record_collaborators WHERE record_id = %s", (rid,)
                    )
                    db.execute(
                        "UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s",
                        (rid,),
                    )
                    db.commit()
        finally:
            _delete_user(no_update_user["id"], no_update_user["username"])
    finally:
        _delete_role(role_id)


def test_collaborator_view_only_cannot_edit(
    client, admin_api_key, admin_user_id
):
    """can_view=true / can_edit=false → 不管角色有无 update,均 403。"""
    from hermes.crm.db import db_session

    # Sales Manager 有 role update,但 can_edit=False
    viewer = _make_user("Sales Manager")
    try:
        suffix = secrets.token_hex(4)
        with db_session() as db:
            row = db.fetchone(
                """
                INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
                VALUES (%s, %s, %s, FALSE)
                RETURNING accounts_id
                """,
                (f"QA-ViewOnly-{suffix}", admin_user_id, admin_user_id),
            )
            db.commit()
        rid = int(row["accounts_id"])

        try:
            # 共享 can_edit=False
            r_share = client.post(
                f"/crm/records/accounts/{rid}/collaborators",
                json={"user_id": viewer["id"], "can_view": True, "can_edit": False},
                headers=_auth(admin_api_key),
            )
            assert r_share.status_code == 200, (
                f"POST collaborator failed: {r_share.status_code}: {r_share.text}"
            )

            # viewer 发 PUT → can_edit=False → 403
            r_put = client.put(
                f"/crm/records/accounts/{rid}",
                json={"accounts_name": f"should-fail-{suffix}"},
                headers=_auth(viewer["api_key"]),
            )
            assert r_put.status_code == 403, (
                f"expected 403 (can_edit=false), "
                f"got {r_put.status_code}: {r_put.text}"
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
        _delete_user(viewer["id"], viewer["username"])


def test_collaborator_can_edit_can_undo(
    client, admin_api_key, admin_user_id
):
    """双达 + undo:can_edit=true + role.update → editor PUT 后 undo 应 200。"""
    from hermes.crm.db import db_session

    editor = _make_user("Sales Manager")
    try:
        suffix = secrets.token_hex(4)
        with db_session() as db:
            row = db.fetchone(
                """
                INSERT INTO accounts (accounts_name, owner_by, created_by, deleted)
                VALUES (%s, %s, %s, FALSE)
                RETURNING accounts_id
                """,
                (f"QA-Undo-{suffix}", admin_user_id, admin_user_id),
            )
            db.commit()
        rid = int(row["accounts_id"])

        try:
            r_share = client.post(
                f"/crm/records/accounts/{rid}/collaborators",
                json={"user_id": editor["id"], "can_view": True, "can_edit": True},
                headers=_auth(admin_api_key),
            )
            assert r_share.status_code == 200, (
                f"POST collaborator failed: {r_share.status_code}: {r_share.text}"
            )

            r_put = client.put(
                f"/crm/records/accounts/{rid}",
                json={"accounts_name": f"edited-{suffix}"},
                headers=_auth(editor["api_key"]),
            )
            assert r_put.status_code == 200, (
                f"PUT precondition failed: {r_put.status_code}: {r_put.text}"
            )

            r_undo = client.post(
                f"/crm/records/accounts/{rid}/undo",
                headers=_auth(editor["api_key"]),
            )
            assert r_undo.status_code == 200, (
                f"expected 200 (collaborator can_edit+role.update can undo), "
                f"got {r_undo.status_code}: {r_undo.text}"
            )
            body = r_undo.json()
            assert body.get("success") is True
            assert "accounts_name" in body.get("reverted_fields", [])
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
        _delete_user(editor["id"], editor["username"])
