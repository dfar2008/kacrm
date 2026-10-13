"""Route-level tests: POST /crm/modules/{m}/fields delegates to
provision_field (the shared helper), fixing the historical bug where
the direct route only wrote metadata and never created the column.

These patch hermes_cloud.app.crm.field_provisioning.provision_field
and the route's auth/db deps so no live DB is needed.

Fixture adaptation notes vs the plan text:
- ``_resolve_module`` is defined INSIDE build_router() closure and is
  NOT patchable via patch.object(routes_mod, "_resolve_module", ...).
  Instead we patch ``ModulesDb`` at the hermes.crm.models level so the
  closure-internal _resolve_module returns the fake module.
- ``ModuleFieldsDb`` is also patched so the dup-check (get_field_by_name)
  returns None (no duplicate).
- ``_require_admin`` IS module-level and is patchable directly.
- ``get_db`` IS module-level and is overridable via dependency_overrides.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def client_and_spy():
    """Build the CRM router with auth + db + provision_field stubbed.

    Yields (TestClient, provision_spy).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import hermes_cloud.app.crm.routes as routes_mod

    provision_spy = MagicMock(
        return_value={"field_id": 101, "field_name": "region"})

    fake_db = MagicMock()
    fake_module = {"id": 3, "name": "accounts", "table_name": "accounts"}

    # ModulesDb.get_module_by_name is called by _resolve_module (closure).
    fake_modules_db = MagicMock()
    fake_modules_db.return_value.get_module_by_name.return_value = fake_module

    # ModuleFieldsDb.get_field_by_name is called for dup check — return None.
    fake_module_fields_db = MagicMock()
    fake_module_fields_db.return_value.get_field_by_name.return_value = None

    app = FastAPI()
    router = routes_mod.build_router()
    app.include_router(router, prefix="")

    # Override the FastAPI deps the route declares.
    from hermes.crm.db import get_db
    from hermes_cloud.app.crm.routes import _require_admin

    fake_admin = {"id": 9, "username": "admin", "is_admin": True}
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[_require_admin] = lambda: fake_admin

    with patch("hermes_cloud.app.crm.routes.ModulesDb", fake_modules_db), \
         patch("hermes_cloud.app.crm.routes.ModuleFieldsDb",
               fake_module_fields_db), \
         patch("hermes_cloud.app.crm.routes.provision_field",
               provision_spy):
        yield TestClient(app), provision_spy


def test_add_module_field_calls_provision_field(client_and_spy):
    client, spy = client_and_spy
    resp = client.post(
        "/crm/modules/accounts/fields",
        json={"field_name": "region", "field_label": "Region",
              "field_type": "text"},
    )
    assert resp.status_code == 200
    spy.assert_called_once()
    # provision_field(db, module, field_data, actor, reason)
    _db, module, field_data, _actor, _reason = spy.call_args[0]
    assert module["name"] == "accounts"
    assert field_data["field_name"] == "region"
    assert field_data["field_type"] == "text"


def test_add_module_field_returns_id_from_helper(client_and_spy):
    client, spy = client_and_spy
    resp = client.post(
        "/crm/modules/accounts/fields",
        json={"field_name": "region", "field_label": "Region",
              "field_type": "text"},
    )
    assert resp.json() == {
        "ok": True, "id": 101, "field_name": "region"}
