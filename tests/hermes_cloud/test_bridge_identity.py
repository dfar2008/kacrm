"""Tests for :mod:`hermes_cloud.app.bridge.identity`.

Phase 1d. Exercises the three public helpers — ``current_user``,
``crm_context``, ``ensure_crm_identity`` — against the real
``hermes_crm_dev`` database (skip-if-unreachable, same policy as the
other ``test_crm_*`` modules).

Hygiene: every test creates its own user via :class:`CrmUsersAuthStore`
and soft-deletes in teardown. CRM role / department seed rows are left
alone — they're shared fixtures that predate this test file.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_store():
    from hermes_cloud.app.crm_auth_store import CrmUsersAuthStore

    return CrmUsersAuthStore()


@pytest.fixture
def test_user(auth_store):
    """Create a throw-away user; soft-delete on teardown."""
    from hermes.crm.db import db_session

    email = f"pqa-bridge-{secrets.token_hex(4)}@example.com"
    acct = auth_store.register_user(email, "pw")
    yield acct
    # Delete the profile row first — boot warm-up in main.py respawns
    # a gateway for any profile whose owner is soft-deleted, creating
    # ~/.hermes/profiles/<id>/ orphans on the next cloud restart.
    with db_session() as db:
        db.execute(
            "DELETE FROM profiles WHERE id IN "
            "(SELECT username FROM users WHERE id = %s)", (int(acct.id),),
        )
        db.execute(
            "UPDATE users SET deleted = TRUE WHERE id = %s", (int(acct.id),)
        )


@pytest.fixture
def fake_request(auth_store):
    """Minimal FastAPI-request stand-in: only the fields ``current_user``
    and ``require_bearer_auth`` touch — headers, app.state, client.

    We build it manually rather than spinning up a full ``create_app``
    because this keeps the test focused on the bridge layer. A
    separate integration test (``test_current_user_via_create_app``)
    exercises the boot wiring.
    """
    from types import SimpleNamespace

    state = SimpleNamespace(auth_store=auth_store, config=None)
    app = SimpleNamespace(state=state)

    def _make(headers: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            app=app,
            headers=headers or {},
            cookies={},
            query_params={},
            client=SimpleNamespace(host="127.0.0.1"),
        )

    return _make


# ---------------------------------------------------------------------------
# current_user
# ---------------------------------------------------------------------------


def test_current_user_resolves_valid_bearer(fake_request, test_user):
    from hermes_cloud.app.bridge.identity import current_user

    req = fake_request({"Authorization": f"Bearer {test_user.api_key}"})
    got = current_user(req)
    assert got.id == test_user.id
    assert got.email == test_user.email


def test_current_user_rejects_missing_bearer(fake_request, test_user):
    from hermes_cloud.app.bridge.identity import current_user

    # Populate the store so ``has_user_keys`` is True (auth_required).
    req = fake_request({})
    with pytest.raises(HTTPException) as exc:
        current_user(req)
    assert exc.value.status_code == 401


def test_current_user_rejects_unknown_bearer(fake_request, test_user):
    from hermes_cloud.app.bridge.identity import current_user

    req = fake_request({"Authorization": "Bearer deadbeef-nope"})
    with pytest.raises(HTTPException) as exc:
        current_user(req)
    assert exc.value.status_code == 401


def test_current_user_rejects_soft_deleted(
    fake_request, test_user, auth_store
):
    from hermes_cloud.app.bridge.identity import current_user

    auth_store.delete_user(test_user.id)
    req = fake_request({"Authorization": f"Bearer {test_user.api_key}"})
    with pytest.raises(HTTPException) as exc:
        current_user(req)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# crm_context
# ---------------------------------------------------------------------------


def test_crm_context_shape_for_fresh_user(test_user):
    """A fresh user has no role_id / department_id until an admin grants
    them. Context should still build (defaults everywhere)."""
    from hermes_cloud.app.bridge.identity import CrmContext, crm_context

    ctx = crm_context(test_user.id)
    assert isinstance(ctx, CrmContext)
    assert ctx.user_id == int(test_user.id)
    assert ctx.is_admin is False
    assert ctx.role_id is None
    assert ctx.role_name == ""
    assert ctx.department_id is None
    assert ctx.department_name == ""
    assert ctx.team_ids == []


def test_crm_context_joins_role_and_department(test_user):
    """Assign a role + department and verify the join picks them up."""
    from hermes_cloud.app.bridge.identity import crm_context
    from hermes.crm.db import db_session

    with db_session() as db:
        role = db.fetchone(
            "SELECT id, name FROM roles WHERE name = %s", ("Sales",)
        )
        dept = db.fetchone(
            "SELECT id, name FROM departments WHERE name = %s", ("Company",)
        )
        assert role and dept, "seed data missing"
        db.execute(
            "UPDATE users SET role_id = %s, department_id = %s WHERE id = %s",
            (int(role["id"]), int(dept["id"]), int(test_user.id)),
        )

    ctx = crm_context(test_user.id)
    assert ctx is not None
    assert ctx.role_id == int(role["id"])
    assert ctx.role_name == "Sales"
    assert ctx.department_id == int(dept["id"])
    assert ctx.department_name == "Company"
    assert ctx.team_ids == [int(dept["id"])]


def test_crm_context_admin_flag(auth_store, test_user):
    from hermes_cloud.app.bridge.identity import crm_context

    auth_store.update_user(test_user.id, is_admin=True)
    ctx = crm_context(test_user.id)
    assert ctx is not None and ctx.is_admin is True


def test_crm_context_returns_none_for_non_integer_user_id():
    from hermes_cloud.app.bridge.identity import crm_context

    assert crm_context("not-an-int") is None


def test_crm_context_returns_none_for_soft_deleted(auth_store, test_user):
    from hermes_cloud.app.bridge.identity import crm_context

    auth_store.delete_user(test_user.id)
    assert crm_context(test_user.id) is None


def test_crm_context_returns_none_for_unknown_user_id():
    from hermes_cloud.app.bridge.identity import crm_context

    assert crm_context("99999999999") is None


# ---------------------------------------------------------------------------
# ensure_crm_identity
# ---------------------------------------------------------------------------


def test_ensure_crm_identity_assigns_default_role(test_user):
    from hermes_cloud.app.bridge.identity import (
        DEFAULT_CRM_ROLE_NAME,
        ensure_crm_identity,
    )
    from hermes.crm.db import db_session

    role_id = ensure_crm_identity(test_user.id)
    assert role_id is not None
    with db_session() as db:
        row = db.fetchone(
            "SELECT r.name FROM users u LEFT JOIN roles r ON u.role_id = r.id "
            "WHERE u.id = %s",
            (int(test_user.id),),
        )
    assert row is not None and row["name"] == DEFAULT_CRM_ROLE_NAME


def test_ensure_crm_identity_is_idempotent(test_user):
    from hermes_cloud.app.bridge.identity import ensure_crm_identity

    first = ensure_crm_identity(test_user.id)
    second = ensure_crm_identity(test_user.id)
    assert first == second and first is not None


def test_ensure_crm_identity_preserves_existing_role(test_user):
    """If an admin has already assigned a non-default role, don't stomp
    it — idempotence means "leave alone", not "reset to default"."""
    from hermes_cloud.app.bridge.identity import ensure_crm_identity
    from hermes.crm.db import db_session

    with db_session() as db:
        mgr = db.fetchone(
            "SELECT id FROM roles WHERE name = %s", ("Sales Manager",)
        )
        assert mgr is not None
        db.execute(
            "UPDATE users SET role_id = %s WHERE id = %s",
            (int(mgr["id"]), int(test_user.id)),
        )

    returned = ensure_crm_identity(test_user.id)
    assert returned == int(mgr["id"])

    with db_session() as db:
        row = db.fetchone(
            "SELECT role_id FROM users WHERE id = %s", (int(test_user.id),)
        )
    assert row is not None and int(row["role_id"]) == int(mgr["id"])


def test_ensure_crm_identity_noops_on_bad_user_id():
    from hermes_cloud.app.bridge.identity import ensure_crm_identity

    assert ensure_crm_identity("not-an-int") is None
    assert ensure_crm_identity("99999999999") is None


# ---------------------------------------------------------------------------
# create_app integration — current_user picks up the real auth store
# ---------------------------------------------------------------------------


def test_current_user_via_create_app(test_user):
    """End-to-end: create_app() with a Postgres session_store_url wires up
    ``CrmUsersAuthStore``; ``current_user`` resolves a real Bearer token
    against that store."""
    from fastapi import Request

    from hermes_cloud.app.bridge.identity import current_user
    from hermes_cloud.app.config import AppConfig
    from hermes.crm.db import _resolve_dsn
    from hermes_cloud.app.main import create_app

    dsn = _resolve_dsn().replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    app = create_app(AppConfig(session_store_url=dsn))

    # Build a minimal Request pointing at the just-booted app.
    scope = {
        "type": "http",
        "app": app,
        "headers": [
            (b"authorization", f"Bearer {test_user.api_key}".encode()),
        ],
        "query_string": b"",
        "path": "/",
        "client": ("127.0.0.1", 0),
    }
    req = Request(scope)
    got = current_user(req)
    assert got.id == test_user.id
