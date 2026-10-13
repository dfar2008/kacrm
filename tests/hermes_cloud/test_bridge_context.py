"""Tests for :mod:`hermes_cloud.app.bridge.context`.

Phase 1e. Exercises :func:`get_context` end-to-end against a real
``create_app`` boot (same scheme-gated setup the CRM tests use) so
``auth_store`` is :class:`CrmUsersAuthStore` and ``profile_store`` is
:class:`CrmProfileStore`.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi import Request
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


@pytest.fixture(scope="module")
def app():
    from hermes_cloud.app.config import AppConfig
    from hermes.crm.db import _resolve_dsn
    from hermes_cloud.app.main import create_app

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return create_app(AppConfig(session_store_url=dsn))


@pytest.fixture
def fresh_user(app):
    """Create a throw-away user + default profile; soft-delete on teardown."""
    from hermes.crm.db import db_session

    auth_store = app.state.auth_store
    profile_store = app.state.profile_store
    acct = auth_store.register_user(
        f"pqa-ctx-{secrets.token_hex(4)}@example.com", "pw"
    )
    profile_store.ensure_default_profile(user_id=acct.id)
    yield acct
    # Delete the profile row first — without this the boot-time
    # warm-up in main.py respawns a gateway for the ghost user on
    # the next cloud restart, which creates ~/.hermes/profiles/<id>/
    # and clutters the admin user list with orphan dirs.
    with db_session() as db:
        db.execute(
            "DELETE FROM profiles WHERE id IN "
            "(SELECT username FROM users WHERE id = %s)", (int(acct.id),),
        )
        db.execute(
            "UPDATE users SET deleted = TRUE WHERE id = %s", (int(acct.id),)
        )


def _make_request(app, headers: dict[str, str] | None = None) -> Request:
    """Minimal Request with app state wired up."""
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    scope = {
        "type": "http",
        "app": app,
        "headers": raw_headers,
        "query_string": b"",
        "path": "/",
        "client": ("127.0.0.1", 0),
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# get_context happy path
# ---------------------------------------------------------------------------


def test_get_context_resolves_user_and_default_profile(app, fresh_user):
    from hermes_cloud.app.bridge.context import RequestContext, get_context

    req = _make_request(app, {"Authorization": f"Bearer {fresh_user.api_key}"})
    ctx = get_context(req)
    assert isinstance(ctx, RequestContext)
    assert ctx.user_id == fresh_user.id
    assert ctx.is_admin is False
    # ensure_default_profile uses profile.id == user.id for the default row
    assert ctx.profile_id == fresh_user.id
    assert ctx.source == "manual"


def test_get_context_surfaces_admin_flag(app, fresh_user):
    from hermes_cloud.app.bridge.context import get_context

    app.state.auth_store.update_user(fresh_user.id, is_admin=True)
    req = _make_request(app, {"Authorization": f"Bearer {fresh_user.api_key}"})
    ctx = get_context(req)
    assert ctx.is_admin is True


def test_get_context_profile_id_none_when_no_default(app, fresh_user):
    """Deleting the default row (edge case) returns profile_id=None
    rather than crashing."""
    from hermes_cloud.app.bridge.context import get_context
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "UPDATE profiles SET is_active = FALSE WHERE user_id = %s",
            (int(fresh_user.id),),
        )
    req = _make_request(app, {"Authorization": f"Bearer {fresh_user.api_key}"})
    ctx = get_context(req)
    assert ctx.user_id == fresh_user.id
    assert ctx.profile_id is None


# ---------------------------------------------------------------------------
# Source provenance
# ---------------------------------------------------------------------------


def test_get_context_accepts_agent_source_header(app, fresh_user):
    from hermes_cloud.app.bridge.context import get_context

    req = _make_request(
        app,
        {
            "Authorization": f"Bearer {fresh_user.api_key}",
            "X-Source": "agent:default/field-extractor",
        },
    )
    ctx = get_context(req)
    assert ctx.source == "agent:default/field-extractor"


def test_get_context_accepts_feishu_source_header(app, fresh_user):
    from hermes_cloud.app.bridge.context import get_context

    req = _make_request(
        app,
        {
            "Authorization": f"Bearer {fresh_user.api_key}",
            "X-Source": "feishu:msg_abc123",
        },
    )
    ctx = get_context(req)
    assert ctx.source == "feishu:msg_abc123"


def test_get_context_rejects_unknown_source_prefix(app, fresh_user):
    """Anything outside the allowlist falls back to ``manual`` — prevents
    a malicious caller from forging audit stamps."""
    from hermes_cloud.app.bridge.context import get_context

    req = _make_request(
        app,
        {
            "Authorization": f"Bearer {fresh_user.api_key}",
            "X-Source": "root:shady",
        },
    )
    ctx = get_context(req)
    assert ctx.source == "manual"


def test_get_context_manual_is_the_default(app, fresh_user):
    from hermes_cloud.app.bridge.context import get_context

    req = _make_request(
        app,
        {
            "Authorization": f"Bearer {fresh_user.api_key}",
            "X-Source": "   ",  # whitespace-only → fall back
        },
    )
    ctx = get_context(req)
    assert ctx.source == "manual"


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_get_context_requires_bearer(app):
    from hermes_cloud.app.bridge.context import get_context

    req = _make_request(app, {})
    with pytest.raises(HTTPException) as exc:
        get_context(req)
    assert exc.value.status_code == 401


def test_get_context_rejects_unknown_bearer(app):
    from hermes_cloud.app.bridge.context import get_context

    req = _make_request(app, {"Authorization": "Bearer deadbeef-nope"})
    with pytest.raises(HTTPException) as exc:
        get_context(req)
    assert exc.value.status_code == 401
