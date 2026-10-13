"""End-to-end RBAC tests.

Exercises the real /crm/auth/login → bearer-token → /crm/me/permissions path
against hermes_crm_dev. A second non-admin user is created per-run with a
fixed Sales role so we can assert that the effective-permissions response
narrows correctly by role.

Skipped automatically if the CRM DB can't be reached — the same test suite
is run by folks who haven't provisioned PostgreSQL locally.
"""
from __future__ import annotations

import os
import secrets

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


# Charter v2 Phase 4b moved hermes_cloud.app.crm.db → hermes.crm.db. The
# old ``_crm_reachable()`` here caught the ImportError silently and always
# returned False, so this suite skipped on every run. Switched to the
# conftest-managed ``requires_real_pg`` marker, which is the canonical
# way to gate live-DB tests in this repo.
pytestmark = pytest.mark.requires_real_pg


@pytest.fixture(scope="module")
def client() -> TestClient:
    # Phase 1d: the /crm/* routes now resolve Bearer tokens via
    # :func:`hermes_cloud.app.bridge.identity.current_user`, which delegates
    # to ``app.state.auth_store``. Passing the PG DSN promotes the store
    # to :class:`CrmUsersAuthStore` so the seed admin's api_key resolves.
    from hermes.crm.db import _resolve_dsn

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return TestClient(create_app(AppConfig(session_store_url=dsn)))


@pytest.fixture(scope="module")
def admin_api_key() -> str:
    """Pull the admin api_key straight from the DB — seed sets one up."""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT api_key FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row and row.get("api_key"), "admin user missing api_key — run seed"
    return row["api_key"]


@pytest.fixture(scope="module")
def sales_user() -> dict:
    """Create a per-run Sales-role user with a fresh api_key.

    Cleaned up on teardown — leaking these rows caused boot-time
    warm-up to resurrect a gateway for every dead sales_e2e_* on
    each cloud restart, cluttering ~/.hermes/profiles/ and the admin
    user list.
    """
    from hermes.crm.db import db_session
    from hermes.crm.models.users_db import UsersDb

    username = f"sales_e2e_{secrets.token_hex(4)}"
    api_key = secrets.token_hex(24)

    with db_session() as db:
        role_id = db.fetchcolumn(
            "SELECT id FROM roles WHERE name = 'Sales' LIMIT 1"
        )
        assert role_id, "Sales role missing — run seed"

        users = UsersDb(db)
        uid = users.insert(
            {
                "username": username,
                "password": UsersDb.hash_password("password123"),
                "realname": "Sales E2E",
                "email": f"{username}@example.test",
                "status": 1,
                "is_admin": False,
                "api_key": api_key,
                "plan": "free",
                "role_id": int(role_id),
            }
        )
        db.commit()

    yield {"id": uid, "username": username, "api_key": api_key}

    with db_session() as db:
        db.execute("DELETE FROM profiles WHERE id = %s", (username,))
        db.execute("UPDATE users SET deleted = TRUE WHERE id = %s", (int(uid),))


# ----------------------------------------------------------------------
# Admin path — sees every collection × every permission
# ----------------------------------------------------------------------


def test_admin_permissions_map_lists_every_collection_and_verb(
    client: TestClient, admin_api_key: str
) -> None:
    resp = client.get(
        "/crm/me/permissions",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["is_admin"] is True
    # Admin's map must include every seeded collection...
    for col in (
        "accounts",
        "contacts",
        "leads",
        "users",
        "roles",
        "departments",
    ):
        assert col in body["modules"], f"admin missing collection: {col}"
    # ...and within a given collection, every permission verb.
    accounts_perms = set(body["modules"]["accounts"])
    for verb in ("index", "create", "update", "delete", "view", "export"):
        assert verb in accounts_perms, f"admin missing accounts.{verb}"


def test_admin_point_check_accounts_delete_allowed(
    client: TestClient, admin_api_key: str
) -> None:
    resp = client.get(
        "/crm/me/permissions/accounts/delete",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "module": "accounts",
        "action": "delete",
        "allowed": True,
    }


# ----------------------------------------------------------------------
# Sales role path — matches the seeded DEFAULT_ROLE_GRANTS matrix exactly
# ----------------------------------------------------------------------


def test_sales_permissions_map_matches_seeded_matrix(
    client: TestClient, sales_user: dict
) -> None:
    resp = client.get(
        "/crm/me/permissions",
        headers={"Authorization": f"Bearer {sales_user['api_key']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["is_admin"] is False
    assert body["role_name"] == "Sales"

    mods = body["modules"]
    # Sales has exactly these verbs on accounts per DEFAULT_ROLE_GRANTS.
    assert set(mods.get("accounts", [])) == {"index", "create", "update", "view"}
    # Sales does NOT get delete/export on accounts.
    assert "delete" not in mods.get("accounts", [])
    assert "export" not in mods.get("accounts", [])
    # Sales is not granted anything on quotes beyond {index, create, view}.
    assert set(mods.get("quotes", [])) == {"index", "create", "view"}


def test_sales_point_check_accounts_delete_denied(
    client: TestClient, sales_user: dict
) -> None:
    resp = client.get(
        "/crm/me/permissions/accounts/delete",
        headers={"Authorization": f"Bearer {sales_user['api_key']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is False


def test_sales_point_check_accounts_update_allowed(
    client: TestClient, sales_user: dict
) -> None:
    resp = client.get(
        "/crm/me/permissions/accounts/update",
        headers={"Authorization": f"Bearer {sales_user['api_key']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["allowed"] is True


def test_sales_user_cannot_list_users(
    client: TestClient, sales_user: dict
) -> None:
    # /crm/users requires admin via _require_admin.
    resp = client.get(
        "/crm/users",
        headers={"Authorization": f"Bearer {sales_user['api_key']}"},
    )
    assert resp.status_code == 403


def test_unauthenticated_permissions_call_rejected(client: TestClient) -> None:
    resp = client.get("/crm/me/permissions")
    assert resp.status_code == 401


# ----------------------------------------------------------------------
# Catalog endpoints — authenticated users can read
# ----------------------------------------------------------------------


def test_list_roles_returns_seeded_roles(
    client: TestClient, admin_api_key: str
) -> None:
    resp = client.get(
        "/crm/roles",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert {"General Manager", "Sales", "Support"}.issubset(names)


def test_list_permissions_returns_full_verb_catalog(
    client: TestClient, admin_api_key: str
) -> None:
    resp = client.get(
        "/crm/permissions",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    assert resp.status_code == 200
    codes = {p["code"] for p in resp.json()}
    for verb in (
        "index",
        "create",
        "update",
        "delete",
        "view",
        "export",
        "massdelete",
    ):
        assert verb in codes
