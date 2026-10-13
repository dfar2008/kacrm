"""Admin CRUD for lost_reasons — PR-1 of the governance plan.

No is_system protection here (all rows editable/deletable). Tests cover
seed presence, CRUD happy path, and in-use deletion rejection.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


pytestmark = pytest.mark.requires_real_pg


@pytest.fixture(scope="module")
def client() -> TestClient:
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


def _h(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def test_list_returns_seed_rows(client: TestClient, admin_api_key: str) -> None:
    r = client.get("/api/admin/lost-reasons", headers=_h(admin_api_key))
    assert r.status_code == 200, r.text
    by_name = {x["name"] for x in r.json()["reasons"]}
    for n in (
        "price_too_high", "lost_to_competitor", "no_budget",
        "wrong_timing", "no_decision_maker", "other",
    ):
        assert n in by_name


def test_crud_happy_path(client: TestClient, admin_api_key: str) -> None:
    suffix = secrets.token_hex(3)
    name = f"compliance_fail_{suffix}"

    # Create
    r = client.post(
        "/api/admin/lost-reasons",
        headers=_h(admin_api_key),
        json={"name": name, "label": "Compliance failure", "sort_order": 25},
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    # Update label
    r = client.put(
        f"/api/admin/lost-reasons/{new_id}",
        headers=_h(admin_api_key),
        json={"label": "Compliance / regulatory"},
    )
    assert r.status_code == 200, r.text

    # Delete (no opportunities reference it)
    r = client.delete(f"/api/admin/lost-reasons/{new_id}", headers=_h(admin_api_key))
    assert r.status_code == 204, r.text


def test_duplicate_name_rejected(client: TestClient, admin_api_key: str) -> None:
    r = client.post(
        "/api/admin/lost-reasons",
        headers=_h(admin_api_key),
        json={"name": "no_budget", "label": "Whatever"},
    )
    assert r.status_code == 409, r.text


def test_unauthenticated_rejected(client: TestClient) -> None:
    r = client.get("/api/admin/lost-reasons")
    assert r.status_code in (401, 403)


def test_update_unknown_field_rejected(client: TestClient, admin_api_key: str) -> None:
    from hermes.crm.db import db_session
    with db_session() as db:
        rid = int(db.fetchcolumn("SELECT id FROM lost_reasons WHERE name = 'no_budget'"))
    r = client.put(
        f"/api/admin/lost-reasons/{rid}",
        headers=_h(admin_api_key),
        json={"is_system": True},
    )
    assert r.status_code == 400, r.text
