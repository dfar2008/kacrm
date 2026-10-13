from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient


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
    from hermes_cloud.app.config import AppConfig
    from hermes.crm.db import _resolve_dsn
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


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def test_reclassify_requires_fields(client: TestClient, admin_api_key: str) -> None:
    r = client.post("/api/touchpoints/reclassify", json={}, headers=_auth(admin_api_key))
    assert r.status_code == 400


def test_reclassify_rejects_missing_parent(
    client: TestClient,
    admin_api_key: str,
) -> None:
    r = client.post(
        "/api/touchpoints/reclassify",
        json={
            "derived_from": "00000000-0000-0000-0000-000000000000",
            "new_kind": "call_log",
            "summary": "x",
        },
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 404


def test_reclassify_creates_child_interaction_with_derived_from(
    client: TestClient,
    admin_api_key: str,
) -> None:
    from hermes.crm.db import db_session
    from hermes_cloud.app.interactions import emit

    probe_id = 1_000_000 + int(secrets.token_hex(3), 16)
    parent_id = emit(
        kind="chat",
        subjects=[{"module": "probe", "id": probe_id}],
        actors=[{"type": "user", "id": 1}],
        content={"text": "我刚和王总打完电话"},
        source="chat",
    )

    child_id = None
    try:
        r = client.post(
            "/api/touchpoints/reclassify",
            json={
                "derived_from": parent_id,
                "new_kind": "call_log",
                "summary": "客户电话沟通",
                "channel": "phone",
            },
            headers=_auth(admin_api_key),
        )
        assert r.status_code == 200, r.text
        child_id = r.json()["data"]["interaction_id"]

        with db_session() as db:
            row = db.fetchone(
                "SELECT kind, derived_from, source, subjects, content "
                "FROM interactions WHERE id = %s",
                (child_id,),
            )
        assert row is not None
        assert row["kind"] == "call_log"
        assert str(row["derived_from"]) == parent_id
        assert row["source"] == "reclassify"
        assert row["subjects"] == [{"module": "probe", "id": probe_id}]
    finally:
        with db_session() as db:
            if child_id:
                db.execute("DELETE FROM interactions WHERE id = %s", (child_id,))
            db.execute("DELETE FROM interactions WHERE id = %s", (parent_id,))
