import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


def test_dashboard_serves_spa():
    """GET /dashboard returns the SPA index.html (client-side routing)."""
    app = create_app(AppConfig(api_keys={"sk-test": "user-1"}))
    client = TestClient(app)

    resp = client.get("/dashboard", params={"api_key": "sk-test"})

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_dashboard_subpath_serves_spa():
    """Any /dashboard/* path serves the SPA."""
    app = create_app(AppConfig(api_keys={"sk-test": "user-1"}))
    client = TestClient(app)

    resp = client.get("/dashboard/settings")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
# dropped (commit 367e344). The endpoints below resolve a profile via
# ``app.state.profile_store``, wired by ``create_app`` only when
# ``HERMES_CRM_DATABASE_URL`` reaches a real PG.
@pytest.mark.requires_real_pg
def test_api_user_config_model_can_be_set_and_retrieved():
    """PUT /api/user-config/model stores config; GET retrieves it."""
    app = create_app(AppConfig(api_keys={"sk-test": "user-1"}))
    client = TestClient(app)

    put_resp = client.put(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "tenant-model",
            "api_key": "provider-key",
            "base_url": "https://provider.example/v1",
        },
    )
    assert put_resp.status_code == 200

    get_resp = client.get(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["model"] == "tenant-model"
    assert data["api_key"] == "provider-key"
    assert data["base_url"] == "https://provider.example/v1"


@pytest.mark.requires_real_pg
def test_session_list_available_via_api():
    """Sessions created via chat completions are listable via the API."""
    app = create_app(AppConfig(api_keys={"sk-test": "user-1"}))
    client = TestClient(app)

    first = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"messages": [{"role": "user", "content": "first"}]},
    )
    second = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"messages": [{"role": "user", "content": "second"}]},
    )

    resp = client.get("/api/sessions", headers={"Authorization": "Bearer sk-test"})

    assert resp.status_code == 200
    data = resp.json()
    conversation_ids = [s["conversation_id"] for s in data["data"]]
    assert first.headers["X-Conversation-Id"] in conversation_ids
    assert second.headers["X-Conversation-Id"] in conversation_ids


# NOTE: ``test_admin_dashboard_serves_spa`` and
# ``test_admin_api_requires_admin_key`` were removed — they passed an
# ``admin_api_keys=`` kwarg to ``AppConfig``, a config field that no longer
# exists. Admin authorization moved to the per-user ``is_admin`` flag on the
# CRM users table; the ``/admin`` HTML route was also retired. Admin API
# authorization is covered by ``test_crm_rbac.py``.
