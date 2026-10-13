"""Interactions event-stream tests.

Scope: exercise the :func:`hermes_cloud.app.interactions.emit` contract
directly, then confirm every CRM write via ``/crm/records/...`` appends
one row to the ``interactions`` table with the expected shape.

Skipped automatically if the CRM DB isn't reachable (same policy as the
RBAC / CRUD suites).
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


def _interactions_for(module: str, record_id: int) -> list[dict]:
    """Fetch interactions whose ``subjects`` contain the given record."""
    from hermes.crm.db import db_session

    with db_session() as db:
        return db.fetchall(
            """
            SELECT id, kind, content, source, actors, subjects, occurred_at
            FROM interactions
            WHERE subjects @> %s::jsonb
            ORDER BY occurred_at ASC
            """,
            (f'[{{"module":"{module}","id":{int(record_id)}}}]',),
        )


# ---------------------------------------------------------------------------
# Direct emitter contract
# ---------------------------------------------------------------------------


def test_emit_writes_row_with_expected_shape():
    from hermes.crm.db import db_session
    from hermes_cloud.app.interactions import emit

    # Unique subject id so cleanup can target this row precisely.
    probe_id = 1_000_000 + int(secrets.token_hex(3), 16)

    uid = emit(
        kind="action",
        subjects=[{"module": "probe", "id": probe_id}],
        actors=[{"type": "user", "id": 1}],
        content={"op": "smoke", "fields": ["alpha", "beta"]},
        source="manual",
        provenance={"privacy_level": "internal"},
    )
    assert isinstance(uid, str) and len(uid) == 36

    try:
        with db_session() as db:
            row = db.fetchone(
                "SELECT kind, source, actors, subjects, content, provenance "
                "FROM interactions WHERE id = %s",
                (uid,),
            )
        assert row is not None
        assert row["kind"] == "action"
        assert row["source"] == "manual"
        assert row["subjects"] == [{"module": "probe", "id": probe_id}]
        assert row["actors"] == [{"type": "user", "id": 1}]
        assert row["content"] == {"op": "smoke", "fields": ["alpha", "beta"]}
        assert row["provenance"] == {"privacy_level": "internal"}
    finally:
        with db_session() as db:
            db.execute("DELETE FROM interactions WHERE id = %s", (uid,))


# ---------------------------------------------------------------------------
# CRM write → interaction row
# ---------------------------------------------------------------------------


def test_create_update_delete_each_emit_one_interaction(client, admin_api_key):
    suffix = secrets.token_hex(4)

    # CREATE
    r = client.post(
        "/crm/records/contacts",
        json={
            "contacts_name": f"InteractionQA {suffix}",
            "email": f"iqa-{suffix}@example.com",
            "owner_by": 1,
        },
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    rows = _interactions_for("contacts", rid)
    assert len(rows) == 1, f"expected 1 interaction after create, got {rows}"
    create_row = rows[0]
    assert create_row["kind"] == "action"
    assert create_row["source"] == "manual"
    assert create_row["actors"] == [{"type": "user", "id": 1}]
    assert create_row["content"]["op"] == "create"
    assert "contacts_name" in create_row["content"]["fields"]
    assert "email" in create_row["content"]["fields"]

    # UPDATE
    r = client.put(
        f"/crm/records/contacts/{rid}",
        json={"email": f"iqa-updated-{suffix}@example.com"},
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text

    rows = _interactions_for("contacts", rid)
    assert len(rows) == 2, f"expected 2 interactions after update, got {rows}"
    update_row = rows[1]
    assert update_row["content"]["op"] == "update"
    assert update_row["content"]["fields"] == ["email"]

    # No-op UPDATE (same email) — must NOT emit a new interaction.
    r = client.put(
        f"/crm/records/contacts/{rid}",
        json={"email": f"iqa-updated-{suffix}@example.com"},
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200, r.text
    rows = _interactions_for("contacts", rid)
    assert len(rows) == 2, "no-op update should not produce a new event"

    # DELETE
    r = client.delete(
        f"/crm/records/contacts/{rid}", headers=_auth(admin_api_key)
    )
    assert r.status_code == 200, r.text

    rows = _interactions_for("contacts", rid)
    assert len(rows) == 3, f"expected 3 interactions after delete, got {rows}"
    delete_row = rows[2]
    assert delete_row["content"] == {"op": "delete"}


def test_subjects_gin_lookup_finds_records_by_module(client, admin_api_key):
    """``subjects @> ...`` uses the GIN index — smoke-test that filter
    pattern since the Phase 2 timeline UI + RL sample builder will rely
    on it."""
    suffix = secrets.token_hex(4)
    r = client.post(
        "/crm/records/accounts",
        json={"accounts_name": f"GinLookup-{suffix}", "owner_by": 1},
        headers=_auth(admin_api_key),
    )
    assert r.status_code == 200
    rid = r.json()["id"]

    from hermes.crm.db import db_session

    with db_session() as db:
        rows = db.fetchall(
            "SELECT id FROM interactions "
            "WHERE subjects @> %s::jsonb",
            (f'[{{"module":"accounts","id":{int(rid)}}}]',),
        )
    assert len(rows) >= 1

    # Cleanup so repeat runs stay clean.
    client.delete(f"/crm/records/accounts/{rid}", headers=_auth(admin_api_key))
