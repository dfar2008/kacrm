"""Tests for X-Profile-Id header routing in main.py (Charter v2).

Charter v2 §简化原则: **1 user = 1 profile**. The bearer token identifies
the *user* (its api_key maps to the decimal ``users.id``); the user's sole
profile is their default, whose ``id == users.username``. The X-Profile-Id
header, when present, MUST name *this* user's own profile (i.e. their
username) and that profile must be active — otherwise ``_resolve_profile_id``
returns 403 (main.py). When omitted, the default profile is resolved (lazily
created via ``ensure_default_profile``).

These tests lock down:
  - No header → default profile (profile_id == username)
  - Valid header naming the user's own profile → that profile is used
  - Header naming another user's profile → 403
  - Header naming an inactive profile → 403
  - Header naming a nonexistent profile → 403
  - Legacy user with no profile row yet → default auto-created on first request

``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
dropped (commit 367e344); ``create_app`` only wires ``CrmProfileStore`` when
``session_store_url`` is a reachable PG. The tests stub
``profile_manager.ensure_running`` and the http client so no real subprocess
is spawned.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

pytestmark = pytest.mark.requires_real_pg


def _make_client(tmp_path, users, captured: list):
    """Build a TestClient wired to the real CRM PG for ``users``.

    ``users`` is a list of ``pg_users`` namespaces. Their tokens are seeded
    into ``AppConfig.api_keys`` mapped to their decimal uid, so
    ``require_bearer_auth`` resolves the integer user-id ``CrmProfileStore``
    expects.

    Returns a context manager yielding ``(client, app)``; stubs for
    ``profile_manager.ensure_running`` / ``http_client`` are applied right
    after lifespan startup so no subprocess spawns and the warm-boot task
    (which sleeps 2s before touching anything) can never call the real one.
    """
    from hermes.crm.db import _resolve_dsn

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    cfg = AppConfig(
        api_keys={u.token: u.uid for u in users},
        internal_key="internal-secret",
        session_store_url=dsn,
        hermes_home_root=str(tmp_path / "hermes_home"),
    )
    app = create_app(cfg)

    async def fake_ensure_running(profile_id, *, user_id=None):
        captured.append({"profile_id": profile_id, "user_id": user_id})
        return 9100

    def fake_touch(profile_id):
        pass

    async def fake_request(method, url, headers=None, content=None, **kwargs):
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"ok":true}',
        )

    async def fake_get(url, headers=None, **kwargs):
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"object":"list","data":[]}',
        )

    class _Ctx:
        def __enter__(self_inner):
            self_inner._client_cm = TestClient(app)
            client = self_inner._client_cm.__enter__()
            # Apply stubs after lifespan has populated app.state.
            app.state.profile_manager.ensure_running = fake_ensure_running  # type: ignore[method-assign]
            app.state.profile_manager.touch = fake_touch  # type: ignore[method-assign]
            app.state.http_client.request = fake_request  # type: ignore[method-assign]
            app.state.http_client.get = fake_get  # type: ignore[method-assign]
            return client, app

        def __exit__(self_inner, *exc):
            return self_inner._client_cm.__exit__(*exc)

    return _Ctx()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_no_profile_header_routes_to_default_profile(tmp_path, pg_users):
    """Without X-Profile-Id, ensure_running is called with the user's default
    profile id (== username)."""
    (alice,) = pg_users(1)
    captured: list[dict] = []
    with _make_client(tmp_path, [alice], captured) as (client, app):
        app.state.profile_store.ensure_default_profile(user_id=alice.uid)
        resp = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {alice.token}"}
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    # default profile id == username, NOT the decimal uid
    assert captured[0]["profile_id"] == alice.username
    assert captured[0]["user_id"] == alice.uid


def test_valid_profile_header_routes_to_that_profile(tmp_path, pg_users):
    """With a valid X-Profile-Id naming the user's own profile, it is used.

    Under v2 the user's sole profile is the default (id == username), so the
    explicit header must equal that username to be accepted."""
    (alice,) = pg_users(1)
    captured: list[dict] = []
    with _make_client(tmp_path, [alice], captured) as (client, app):
        default = app.state.profile_store.ensure_default_profile(user_id=alice.uid)
        resp = client.get(
            "/v1/models",
            headers={
                "Authorization": f"Bearer {alice.token}",
                "X-Profile-Id": default.id,
            },
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    assert captured[0]["profile_id"] == alice.username
    assert captured[0]["user_id"] == alice.uid


def test_profile_header_belonging_to_another_user_is_rejected(tmp_path, pg_users):
    """X-Profile-Id that names another user's profile → 403."""
    alice, bob = pg_users(2)
    captured: list[dict] = []
    with _make_client(tmp_path, [alice, bob], captured) as (client, app):
        app.state.profile_store.ensure_default_profile(user_id=alice.uid)
        app.state.profile_store.ensure_default_profile(user_id=bob.uid)
        resp = client.get(
            "/v1/models",
            headers={
                "Authorization": f"Bearer {alice.token}",
                "X-Profile-Id": bob.username,  # bob's profile, not alice's
            },
        )

    assert resp.status_code == 403
    # ensure_running must NOT have been called
    assert captured == []


def test_inactive_profile_header_is_rejected(tmp_path, pg_users):
    """X-Profile-Id pointing at an inactive profile → 403.

    A user's sole profile is the default and cannot be deleted via the public
    API, so to exercise ``_resolve_profile_id``'s ``is_active`` defence we mark
    the row inactive directly in the DB. (We must NOT issue a header-less
    request first, which would lazily re-activate it via
    ``ensure_default_profile``.)"""
    from hermes.crm.db import db_session

    (alice,) = pg_users(1)
    captured: list[dict] = []
    with _make_client(tmp_path, [alice], captured) as (client, app):
        default = app.state.profile_store.ensure_default_profile(user_id=alice.uid)
        with db_session() as db:
            db.execute(
                "UPDATE profiles SET is_active = FALSE WHERE id = %s",
                (default.id,),
            )
        resp = client.get(
            "/v1/models",
            headers={
                "Authorization": f"Bearer {alice.token}",
                "X-Profile-Id": default.id,
            },
        )

    assert resp.status_code == 403
    assert captured == []


def test_unknown_profile_header_is_rejected(tmp_path, pg_users):
    """X-Profile-Id that doesn't exist → 403."""
    (alice,) = pg_users(1)
    captured: list[dict] = []
    with _make_client(tmp_path, [alice], captured) as (client, app):
        app.state.profile_store.ensure_default_profile(user_id=alice.uid)
        resp = client.get(
            "/v1/models",
            headers={
                "Authorization": f"Bearer {alice.token}",
                "X-Profile-Id": "does-not-exist",
            },
        )

    assert resp.status_code == 403
    assert captured == []


def test_missing_default_profile_is_auto_created(tmp_path, pg_users):
    """A registered user with no profile row yet (e.g. created before the
    backfill) gets one created idempotently on the first request."""
    (alice,) = pg_users(1)
    captured: list[dict] = []
    with _make_client(tmp_path, [alice], captured) as (client, app):
        # Note: NO ensure_default_profile call — simulates a profile-less user.
        resp = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {alice.token}"}
        )
        default = app.state.profile_store.get_default_profile(user_id=alice.uid)

    assert resp.status_code == 200
    assert len(captured) == 1
    assert captured[0]["profile_id"] == alice.username
    assert default is not None
    assert default.id == alice.username
