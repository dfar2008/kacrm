"""跨模块汇总规则的 target 候选端点(/crm/modules/{m}/writeback-targets)。

salesorders 通过 accounts_id/contacts_id/quotes_id/potentials_id 关联到对应模块;
accounts_id_2(次级关联,列名≠accounts_id)引擎不认,不应作为 target 出现。
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


@pytest.fixture(scope="module")
def client() -> TestClient:
    from hermes_cloud.app.config import AppConfig
    from hermes_cloud.app.main import create_app
    from hermes.crm.db import _resolve_dsn

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return TestClient(create_app(AppConfig(session_store_url=dsn)))


@pytest.fixture(scope="module")
def auth():
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT api_key FROM users WHERE username='admin' AND is_admin = TRUE LIMIT 1"
        )
    assert row and row.get("api_key"), "admin user missing"
    return {"Authorization": f"Bearer {row['api_key']}"}


def test_writeback_targets_salesorders(client, auth):
    r = client.get("/crm/modules/salesorders/writeback-targets", headers=auth)
    assert r.status_code == 200
    targets = r.json()["targets"]
    by_mod = {t["module"]: t for t in targets}
    # salesorders 外键指向 accounts/contacts/quotes/potentials
    assert "accounts" in by_mod
    acc = by_mod["accounts"]
    assert acc["fk_field"] == "accounts_id"
    assert isinstance(acc["fields"], list)
    # 次级关联 accounts_id_2 不应产生额外/无效 target(列名≠{target}_id)
    assert all(t["fk_field"].endswith("_id") and not t["fk_field"].endswith("_id_2")
               for t in targets)
