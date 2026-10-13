"""Route test for GET /crm/field_proposals/pending_count.

Backs the admin review-queue badge in the SPA nav — the count of
proposals awaiting review. Admin-only.
"""
from __future__ import annotations

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


def test_pending_count_matches_db(client: TestClient, admin_api_key: str) -> None:
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    r = client.get(
        "/crm/field_proposals/pending_count",
        headers={"Authorization": f"Bearer {admin_api_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "count" in body
    assert isinstance(body["count"], int)
    assert body["count"] == FieldProposalsDb().count_by_status("pending")


def test_pending_count_requires_auth(client: TestClient) -> None:
    r = client.get("/crm/field_proposals/pending_count")
    assert r.status_code in (401, 403), r.text
