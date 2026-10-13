"""End-to-end record CRUD tests.

Exercises the full ``/crm/records/{module_name}/...`` stack —
ListQuery / DetailQuery / EditQuery / Save / ItemDelete plumbed through
the FastAPI router — against hermes_crm_dev.

Skipped automatically if the CRM DB isn't reachable, same policy as the
RBAC suite.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _crm_reachable(), reason="hermes_crm_dev not reachable"
)


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
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT api_key FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row and row.get("api_key"), "admin user missing api_key — run seed"
    return row["api_key"]


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def test_list_accounts_returns_metadata_and_rows(client, admin_api_key):
    r = client.get("/crm/records/accounts", headers=_auth(admin_api_key))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["module_name"] == "accounts"
    assert isinstance(body["fields"], list) and body["fields"]
    assert "data" in body and isinstance(body["data"], list)
    assert "total" in body


def test_create_detail_update_delete_roundtrip(client, admin_api_key):
    suffix = secrets.token_hex(4)
    payload = {
        "contacts_name": f"QA Contact {suffix}",
        "email": f"qa-{suffix}@example.com",
        "mobile": "+1 555-0199",
        "owner_by": 1,
    }

    # CREATE
    r = client.post(
        "/crm/records/contacts",
        json=payload,
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["success"] is True
    rid = created["id"]
    assert isinstance(rid, int) and rid > 0

    # DETAIL
    r = client.get(
        f"/crm/records/contacts/{rid}", headers=_auth(admin_api_key)
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["data"]["contacts_name"] == payload["contacts_name"]
    assert detail["data"]["email"] == payload["email"]
    # owner_by_display should resolve from the admin user
    assert detail["data"].get("owner_by_display") == "Administrator"

    # EDIT form for existing record
    r = client.get(
        f"/crm/records/contacts/{rid}/edit", headers=_auth(admin_api_key)
    )
    assert r.status_code == 200, r.text
    edit = r.json()
    assert edit["data"]["contacts_name"] == payload["contacts_name"]
    assert isinstance(edit["data"]["form_fields"], list)

    # EDIT form for a new record
    r = client.get(
        "/crm/records/contacts/edit", headers=_auth(admin_api_key)
    )
    assert r.status_code == 200, r.text
    new_form = r.json()
    # Admin creating a record pre-fills owner_by with their own id.
    assert new_form["data"].get("owner_by") == 1

    # UPDATE
    r = client.put(
        f"/crm/records/contacts/{rid}",
        json={"email": f"qa-updated-{suffix}@example.com"},
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text

    r = client.get(
        f"/crm/records/contacts/{rid}", headers=_auth(admin_api_key)
    )
    assert r.json()["data"]["email"] == f"qa-updated-{suffix}@example.com"

    # DELETE
    r = client.delete(
        f"/crm/records/contacts/{rid}", headers=_auth(admin_api_key)
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    # Detail after delete returns 404
    r = client.get(
        f"/crm/records/contacts/{rid}", headers=_auth(admin_api_key)
    )
    assert r.status_code == 404


def test_create_rejects_missing_required_field(client, admin_api_key):
    # contacts_name is required
    r = client.post(
        "/crm/records/contacts",
        json={"email": "no-name@example.com"},
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 400, r.text
    body = r.json()
    # HTTPException with a dict ``detail`` is returned as the body as-is.
    detail = body.get("detail", body)
    assert detail["field"] == "contacts_name"


def test_detail_nonexistent_record_returns_404(client, admin_api_key):
    # A record id that was never created must 404 — the detail route must
    # not degrade into a 200 schema-metadata response (regression guard for
    # the soft-deleted-record-returns-200 bug).
    r = client.get(
        "/crm/records/contacts/999999999", headers=_auth(admin_api_key)
    )
    assert r.status_code == 404, r.text


def test_module_fields_endpoint_returns_schema(client, admin_api_key):
    # Dedicated schema-metadata route — works regardless of whether the
    # module has any rows, and is what the Schema Hub builder consumes.
    r = client.get(
        "/crm/modules/contacts/fields", headers=_auth(admin_api_key)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["module_name"] == "contacts"
    assert isinstance(body["fields"], list) and body["fields"]


def test_list_supports_search_and_pagination(client, admin_api_key):
    # Seed a unique row we can search for
    suffix = secrets.token_hex(4)
    name = f"UniqueSeed-{suffix}"
    r = client.post(
        "/crm/records/accounts",
        json={"accounts_name": name, "owner_by": 1},
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200
    rid = r.json()["id"]

    r = client.get(
        f"/crm/records/accounts?search={name}&limit=5",
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["accounts_name"] == name

    # Cleanup
    client.delete(
        f"/crm/records/accounts/{rid}", headers=_auth(admin_api_key)
    )


def _any_product_id() -> int | None:
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT products_id FROM products "
            "WHERE NOT COALESCE(deleted, FALSE) LIMIT 1"
        )
    return int(row["products_id"]) if row else None


def test_salesorder_rejects_create_without_products(client, admin_api_key):
    """A salesorder with no product line must be rejected at create — the
    final_amount escape hatch is gone, so amount-only no longer works."""
    suffix = secrets.token_hex(4)
    r = client.post(
        "/crm/records/salesorders",
        json={
            "salesorders_name": f"QA Empty Order {suffix}",
            "owner_by": 1,
            "final_amount": 9999,  # used to satisfy the old guard; must NOT now
        },
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 400, r.text
    detail = r.json().get("detail", r.json())
    assert detail["field"] == "products"


def test_salesorder_create_with_products_computes_amount_then_delete(
    client, admin_api_key
):
    """Create-with-products succeeds, final_amount is derived server-side
    from the line items, and crm_delete soft-deletes (detail 404 after)."""
    pid = _any_product_id()
    if pid is None:
        pytest.skip("no catalog product available in dev DB")

    suffix = secrets.token_hex(4)
    r = client.post(
        "/crm/records/salesorders",
        json={
            "salesorders_name": f"QA Order {suffix}",
            "owner_by": 1,
            "products": [
                {"products_id": pid, "quantity": 2, "price": 150},
            ],
        },
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert isinstance(rid, int) and rid > 0

    try:
        r = client.get(
            f"/crm/records/salesorders/{rid}", headers=_auth(admin_api_key)
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        # 2 × 150 = 300, computed from lines (NOT the bogus final_amount=1).
        assert float(data["final_amount"]) == 300.0
        assert int(data["total_quantity"]) == 2
    finally:
        r = client.delete(
            f"/crm/records/salesorders/{rid}", headers=_auth(admin_api_key)
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

    # Soft-deleted → detail 404
    r = client.get(
        f"/crm/records/salesorders/{rid}", headers=_auth(admin_api_key)
    )
    assert r.status_code == 404


def test_unknown_module_returns_404(client, admin_api_key):
    r = client.get(
        "/crm/records/nonexistent_module", headers=_auth(admin_api_key)
    )
    assert r.status_code == 404


def test_unauthenticated_list_rejected(client):
    r = client.get("/crm/records/accounts")
    assert r.status_code == 401
