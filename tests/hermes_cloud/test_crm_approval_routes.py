"""审批链 REST 端点真库测试(/crm/approvals/*, /crm/modules/*/approval-config)。

连真 dev 库 hermes_crm_dev;需 HERMES_TESTS_REAL_PG=1。引擎逻辑已在
test_crm_approval_flow 充分覆盖,这里只验 REST 接线 + 鉴权 + 全链路。
"""
from __future__ import annotations

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


pytestmark = [
    pytest.mark.requires_real_pg,
    pytest.mark.skipif(not _crm_reachable(), reason="hermes_crm_dev not reachable"),
]

_SO = "salesorders"


@pytest.fixture(scope="module")
def client() -> TestClient:
    from hermes_cloud.app.config import AppConfig
    from hermes_cloud.app.main import create_app
    from hermes.crm.db import _resolve_dsn

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return TestClient(create_app(AppConfig(session_store_url=dsn)))


@pytest.fixture(scope="module")
def admin():
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id, api_key FROM users WHERE username='admin' "
            "AND is_admin = TRUE LIMIT 1"
        )
    assert row and row.get("api_key"), "admin user missing — run seed"
    return {"id": int(row["id"]), "key": row["api_key"]}


@pytest.fixture
def auth(admin):
    return {"Authorization": f"Bearer {admin['key']}"}


@pytest.fixture(autouse=True)
def _clean(admin):
    from hermes.crm.db import db_session

    def _wipe():
        with db_session() as db:
            mid = db.fetchone("SELECT id FROM modules WHERE name=%s", (_SO,))["id"]
            db.execute(
                "DELETE FROM approval_steps WHERE approval_id IN "
                "(SELECT id FROM approvals WHERE module_id=%s)",
                (mid,),
            )
            db.execute("DELETE FROM approvals WHERE module_id=%s", (mid,))
            db.execute("DELETE FROM approval_configs WHERE module_id=%s", (mid,))

    _wipe()
    yield
    _wipe()


def test_pending_returns_shape(client, auth):
    r = client.get("/crm/approvals/pending", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "count" in body


def test_submit_unknown_module_404(client, auth):
    r = client.post(
        "/crm/approvals/submit",
        json={"module": "nope", "record_id": 1},
        headers=auth,
    )
    assert r.status_code == 404


def test_submit_no_config_skips(client, auth):
    r = client.post(
        "/crm/approvals/submit",
        json={"module": _SO, "record_id": 999500},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json() == {"skipped": "no_config"}


def test_config_roundtrip(client, auth, admin):
    steps = [{"step_name": "经理", "approver_ids": [admin["id"]]}]
    r = client.put(
        f"/crm/modules/{_SO}/approval-config",
        json={"steps": steps, "allow_edit_after_approve": 1},
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["ok"] is True

    g = client.get(f"/crm/modules/{_SO}/approval-config", headers=auth)
    assert g.status_code == 200
    cfg = g.json()["config"]
    assert cfg["allow_edit_after_approve"] == 1
    assert len(cfg["steps"]) == 1


def test_full_submit_then_approve(client, auth, admin):
    # admin 既是提交人也是唯一审批人,可端到端走完。
    client.put(
        f"/crm/modules/{_SO}/approval-config",
        json={"steps": [{"step_name": "经理", "approver_ids": [admin["id"]]}]},
        headers=auth,
    )
    s = client.post(
        "/crm/approvals/submit",
        json={"module": _SO, "record_id": 999501},
        headers=auth,
    )
    assert s.status_code == 200 and s.json()["submitted"] is True

    p = client.get("/crm/approvals/pending", headers=auth).json()
    ids = {int(it["data_id"]) for it in p["items"]}
    assert 999501 in ids

    approval_id = next(
        int(it["id"]) for it in p["items"] if int(it["data_id"]) == 999501
    )
    a = client.post(
        f"/crm/approvals/{approval_id}/approve",
        json={"remark": "ok"},
        headers=auth,
    )
    assert a.status_code == 200
    assert a.json()["status"] == "passed"
