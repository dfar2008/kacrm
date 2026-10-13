from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


def test_me_returns_email_for_password_account():
    app = create_app(AppConfig(allow_registration=True))
    client = TestClient(app)

    register = client.post(
        "/auth/register",
        json={"email": "admin@hermes.local", "password": "admin123"},
    )
    assert register.status_code == 201
    api_key = register.json()["api_key"]

    me = client.get("/api/me", headers={"Authorization": f"Bearer {api_key}"})

    assert me.status_code == 200
    assert me.json()["email"] == "admin@hermes.local"
