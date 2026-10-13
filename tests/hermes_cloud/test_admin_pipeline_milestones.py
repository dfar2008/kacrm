"""Admin CRUD for pipeline_milestones — PR-1 of the governance plan.

Covers seed presence, is_system protection (won / lost cannot be
deleted; kind / name locked on system rows), kind='won'/'lost' rejected
on POST, and CRUD happy path for progressing rows.

Skipped automatically if the CRM DB can't be reached.
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
    r = client.get("/api/admin/pipeline-milestones", headers=_h(admin_api_key))
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {m["name"]: m for m in body["milestones"]}
    # 5 progressing + 2 system terminals
    assert "won" in by_name and by_name["won"]["is_system"] is True
    assert by_name["won"]["kind"] == "won"
    assert by_name["won"]["probability"] == 100
    assert "lost" in by_name and by_name["lost"]["is_system"] is True
    assert by_name["lost"]["kind"] == "lost"
    for n in ("initial_contact", "needs_analysis", "proposal_sent", "negotiation", "closing"):
        assert n in by_name, f"missing seed milestone {n}"
        assert by_name[n]["kind"] == "progressing"
        assert by_name[n]["is_system"] is False


def test_create_progressing_milestone_then_delete(
    client: TestClient, admin_api_key: str
) -> None:
    suffix = secrets.token_hex(3)
    name = f"poc_in_progress_{suffix}"
    r = client.post(
        "/api/admin/pipeline-milestones",
        headers=_h(admin_api_key),
        json={"name": name, "label": "POC In Progress", "probability": 35, "sort_order": 25},
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    # Verify it shows up
    r = client.get("/api/admin/pipeline-milestones", headers=_h(admin_api_key))
    assert any(m["id"] == new_id and m["name"] == name for m in r.json()["milestones"])

    # Delete it (no opportunities reference it)
    r = client.delete(f"/api/admin/pipeline-milestones/{new_id}", headers=_h(admin_api_key))
    assert r.status_code == 204, r.text


def test_create_with_terminal_kind_rejected(
    client: TestClient, admin_api_key: str
) -> None:
    for terminal in ("won", "lost"):
        r = client.post(
            "/api/admin/pipeline-milestones",
            headers=_h(admin_api_key),
            json={"name": f"another_{terminal}", "label": "X", "kind": terminal},
        )
        assert r.status_code == 400, f"{terminal}: {r.text}"
        assert "reserved" in r.json()["error"]["message"]


def test_system_row_cannot_be_deleted(client: TestClient, admin_api_key: str) -> None:
    from hermes.crm.db import db_session
    with db_session() as db:
        won_id = db.fetchcolumn("SELECT id FROM pipeline_milestones WHERE name = 'won'")
    r = client.delete(f"/api/admin/pipeline-milestones/{int(won_id)}", headers=_h(admin_api_key))
    assert r.status_code == 409, r.text
    assert "system" in r.json()["error"]["message"]


def test_system_row_name_kind_locked_but_label_editable(
    client: TestClient, admin_api_key: str
) -> None:
    from hermes.crm.db import db_session
    with db_session() as db:
        won_id = int(db.fetchcolumn("SELECT id FROM pipeline_milestones WHERE name = 'won'"))

    # Can update label (editable)
    r = client.put(
        f"/api/admin/pipeline-milestones/{won_id}",
        headers=_h(admin_api_key),
        json={"label": "成交"},
    )
    assert r.status_code == 200, r.text
    # Revert so test is idempotent
    client.put(
        f"/api/admin/pipeline-milestones/{won_id}",
        headers=_h(admin_api_key),
        json={"label": "Won"},
    )

    # Cannot change name on system row
    r = client.put(
        f"/api/admin/pipeline-milestones/{won_id}",
        headers=_h(admin_api_key),
        json={"name": "won_renamed"},
    )
    assert r.status_code == 400 and "locked" in r.json()["error"]["message"]

    # Cannot change kind on system row
    r = client.put(
        f"/api/admin/pipeline-milestones/{won_id}",
        headers=_h(admin_api_key),
        json={"kind": "progressing"},
    )
    assert r.status_code == 400 and "locked" in r.json()["error"]["message"]


def test_non_system_row_cannot_set_kind_terminal(
    client: TestClient, admin_api_key: str
) -> None:
    from hermes.crm.db import db_session
    with db_session() as db:
        neg_id = int(db.fetchcolumn(
            "SELECT id FROM pipeline_milestones WHERE name = 'negotiation'"
        ))
    r = client.put(
        f"/api/admin/pipeline-milestones/{neg_id}",
        headers=_h(admin_api_key),
        json={"kind": "won"},
    )
    assert r.status_code == 400, r.text
    assert "reserved" in r.json()["error"]["message"]


def test_probability_out_of_range_rejected(
    client: TestClient, admin_api_key: str
) -> None:
    suffix = secrets.token_hex(3)
    r = client.post(
        "/api/admin/pipeline-milestones",
        headers=_h(admin_api_key),
        json={"name": f"bad_prob_{suffix}", "label": "X", "probability": 150},
    )
    assert r.status_code == 400, r.text
    assert "0..100" in r.json()["error"]["message"]


def test_duplicate_name_rejected(client: TestClient, admin_api_key: str) -> None:
    r = client.post(
        "/api/admin/pipeline-milestones",
        headers=_h(admin_api_key),
        json={"name": "negotiation", "label": "Whatever"},
    )
    assert r.status_code == 409, r.text


def test_unauthenticated_rejected(client: TestClient) -> None:
    r = client.get("/api/admin/pipeline-milestones")
    assert r.status_code in (401, 403)
    r = client.post("/api/admin/pipeline-milestones", json={"name": "x", "label": "X"})
    assert r.status_code in (401, 403)
