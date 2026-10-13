import os

import pytest

# ``hermes_cloud.app.dev_server`` builds the app at module import time
# (``app = create_dev_app()`` at module bottom), which trips the CRM
# fail-fast gate when the conftest stubs psycopg. Skip the whole file
# under stub-PG mode so collection doesn't crash; the suite needs a real
# PG to exercise dev-server wiring end-to-end anyway.
if os.environ.get("HERMES_TESTS_REAL_PG") != "1":
    pytest.skip(
        "dev_server boots an app at import time; needs real PG",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient  # noqa: E402

from hermes_cloud.app.auth_store import SqlAlchemyAuthStore  # noqa: E402
from hermes_cloud.app.dev_server import create_dev_app  # noqa: E402

pytestmark = pytest.mark.requires_real_pg


def test_dev_app_root_redirects_to_spa():
    app = create_dev_app()
    client = TestClient(app)

    resp = client.get("/", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers["location"] == "/spa"


def test_dev_app_dashboard_serves_spa():
    app = create_dev_app()
    client = TestClient(app)

    resp = client.get("/dashboard")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_dev_app_checkout_auto_pays_by_default():
    app = create_dev_app()
    client = TestClient(app)

    checkout = client.post(
        "/api/billing/checkout",
        headers={"Authorization": "Bearer dev-key"},
        json={"plan_code": "pro-monthly"},
    )

    assert checkout.status_code == 200
    assert checkout.json()["status"] == "paid"


def test_dev_app_promotes_existing_default_admin_account(tmp_path, monkeypatch):
    db_path = tmp_path / "dev.db"
    db_url = f"sqlite:///{db_path}"
    store = SqlAlchemyAuthStore(db_url)
    account = store.register_user("admin@hermes.local", "admin123", is_admin=False)
    assert account.is_admin is False

    monkeypatch.setenv("HERMES_CLOUD_DB_URL", db_url)
    app = create_dev_app()

    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"email": "admin@hermes.local", "password": "admin123"},
        )
        assert login.status_code == 200
        api_key = login.json()["api_key"]

        me = client.get("/api/me", headers={"Authorization": f"Bearer {api_key}"})

    assert me.status_code == 200
    assert me.json()["email"] == "admin@hermes.local"
    assert me.json()["is_admin"] is True


def test_admin_create_user_starts_process_immediately(tmp_path, monkeypatch):
    db_path = tmp_path / "dev.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("HERMES_CLOUD_DB_URL", db_url)
    app = create_dev_app()

    calls = []

    async def fake_ensure_running(user_id: str):
        calls.append(user_id)
        return 9101

    with TestClient(app) as client:
        app.state.profile_manager.ensure_running = fake_ensure_running
        login = client.post(
            "/auth/login",
            json={"email": "admin@hermes.local", "password": "admin123"},
        )
        assert login.status_code == 200
        admin_key = login.json()["api_key"]

        created = client.post(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"email": "starter@example.com", "password": "password123"},
        )

    assert created.status_code == 201
    assert calls == [created.json()["user_id"]]
