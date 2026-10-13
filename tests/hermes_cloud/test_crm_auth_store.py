"""Tests for :class:`CrmUsersAuthStore`.

Exercises the full ``AuthStore`` protocol surface against the real
``hermes_crm_dev`` database. Same skip-if-unreachable policy as the
other CRM test modules.

Hygiene: every test uses a random hex suffix in the synthesised email
so repeat runs don't collide. Test-created users are soft-deleted in a
teardown fixture (``users.deleted = TRUE``) rather than row-deleted —
foreign-key references from ``changelog`` / ``interactions`` would
otherwise violate.
"""
from __future__ import annotations

import secrets

import pytest

from hermes_cloud.app.auth_store import UserAccount


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


@pytest.fixture
def store():
    from hermes_cloud.app.crm_auth_store import CrmUsersAuthStore

    return CrmUsersAuthStore()


@pytest.fixture
def cleanup_users():
    """Soft-delete every user created during the test."""
    created: list[int] = []
    yield created
    if created:
        from hermes.crm.db import db_session

        with db_session() as db:
            for uid in created:
                db.execute(
                    "UPDATE users SET deleted = TRUE WHERE id = %s", (uid,)
                )


def _unique_email(prefix: str = "qa") -> str:
    return f"{prefix}-{secrets.token_hex(4)}@example.com"


# ---------------------------------------------------------------------------
# register_user / authenticate_with_password
# ---------------------------------------------------------------------------


def test_register_user_returns_user_account_with_api_key(store, cleanup_users):
    email = _unique_email()
    acct = store.register_user(email, "hunter2")
    cleanup_users.append(int(acct.id))

    assert isinstance(acct, UserAccount)
    assert acct.id.isdigit()
    assert acct.email == email
    assert acct.api_key and len(acct.api_key) >= 32
    assert acct.is_active is True
    assert acct.is_admin is False
    assert acct.plan == "free"
    # password_hash is the bcrypt blob stored on ``users.password``
    assert acct.password_hash.startswith(("$2a$", "$2b$", "$2y$"))


def test_register_user_rejects_duplicate_email(store, cleanup_users):
    email = _unique_email()
    acct = store.register_user(email, "hunter2")
    cleanup_users.append(int(acct.id))
    with pytest.raises(ValueError, match="already registered"):
        store.register_user(email, "different-password")


def test_register_user_rejects_empty_inputs(store):
    with pytest.raises(ValueError):
        store.register_user("", "pw")
    with pytest.raises(ValueError):
        store.register_user("x@example.com", "")


def test_authenticate_with_password_accepts_email(store, cleanup_users):
    email = _unique_email()
    acct = store.register_user(email, "hunter2")
    cleanup_users.append(int(acct.id))

    got = store.authenticate_with_password(email, "hunter2")
    assert got is not None and got.id == acct.id


def test_authenticate_with_password_accepts_username(store, cleanup_users):
    """D4: login accepts either email or username."""
    email = _unique_email("userlogin")
    acct = store.register_user(email, "hunter2")
    cleanup_users.append(int(acct.id))

    # Username is derived from the email local-part.
    from hermes.crm.models.users_db import UsersDb
    from hermes.crm.db import db_session

    with db_session() as db:
        row = UsersDb(db).get_user_by_id(int(acct.id))
    assert row is not None
    username = row["username"]

    got = store.authenticate_with_password(username, "hunter2")
    assert got is not None and got.id == acct.id


def test_authenticate_with_password_rejects_wrong_password(
    store, cleanup_users
):
    email = _unique_email()
    acct = store.register_user(email, "hunter2")
    cleanup_users.append(int(acct.id))

    assert store.authenticate_with_password(email, "nope") is None


def test_authenticate_with_password_rejects_disabled_user(
    store, cleanup_users
):
    email = _unique_email()
    acct = store.register_user(email, "hunter2")
    cleanup_users.append(int(acct.id))
    # Flip status=0 (disabled)
    store.update_user(acct.id, is_active=False)

    assert store.authenticate_with_password(email, "hunter2") is None


# ---------------------------------------------------------------------------
# authenticate_user (Bearer token)
# ---------------------------------------------------------------------------


def test_authenticate_user_returns_id_for_valid_token(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw")
    cleanup_users.append(int(acct.id))

    assert store.authenticate_user(acct.api_key) == acct.id


def test_authenticate_user_rejects_unknown_token(store):
    assert store.authenticate_user("deadbeef-nope") is None
    assert store.authenticate_user("") is None


def test_authenticate_user_rejects_soft_deleted_user(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw")
    cleanup_users.append(int(acct.id))
    store.delete_user(acct.id)

    assert store.authenticate_user(acct.api_key) is None


# ---------------------------------------------------------------------------
# Admin flag
# ---------------------------------------------------------------------------


def test_register_admin_and_authenticate_admin(store, cleanup_users):
    acct = store.register_user(
        _unique_email("admin"), "pw", is_admin=True
    )
    cleanup_users.append(int(acct.id))

    assert acct.is_admin is True
    # Bearer auth resolves user id normally.
    assert store.authenticate_user(acct.api_key) == acct.id
    # Admin auth only resolves when is_admin = True.
    assert store.authenticate_admin(acct.api_key) == acct.id


def test_authenticate_admin_rejects_non_admin(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw", is_admin=False)
    cleanup_users.append(int(acct.id))

    assert store.authenticate_admin(acct.api_key) is None


# ---------------------------------------------------------------------------
# seed_user_key / seed_admin_key
# ---------------------------------------------------------------------------


def test_seed_user_key_overwrites_api_key(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw")
    cleanup_users.append(int(acct.id))
    new_token = "seeded-" + secrets.token_hex(8)

    store.seed_user_key(token=new_token, user_id=acct.id)

    # Old token is invalidated.
    assert store.authenticate_user(acct.api_key) is None
    # New token authenticates.
    assert store.authenticate_user(new_token) == acct.id


def test_seed_admin_key_flips_is_admin(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw", is_admin=False)
    cleanup_users.append(int(acct.id))
    token = "admin-" + secrets.token_hex(8)

    store.seed_admin_key(token=token, admin_id=acct.id)

    refreshed = store.get_user(acct.id)
    assert refreshed is not None and refreshed.is_admin is True
    assert store.authenticate_admin(token) == acct.id


def test_seed_user_key_silently_ignores_unknown_user(store):
    # Non-existent user id — should NOT crash.
    store.seed_user_key(token="anything", user_id="99999999")
    # Invalid id — should NOT crash.
    store.seed_user_key(token="anything", user_id="not-an-int")


# ---------------------------------------------------------------------------
# has_user_keys / has_admin_keys / list_user_ids
# ---------------------------------------------------------------------------


def test_has_user_keys_true_when_seed_admin_exists(store):
    # The seed script always provisions at least the ``admin`` user with
    # an api_key, so the fresh DB already returns True.
    assert store.has_user_keys() is True


def test_has_admin_keys_true_when_seed_admin_exists(store):
    assert store.has_admin_keys() is True


def test_list_user_ids_contains_registered_user(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw")
    cleanup_users.append(int(acct.id))

    assert acct.id in store.list_user_ids()


# ---------------------------------------------------------------------------
# get_user / update_user / delete_user / reset_password
# ---------------------------------------------------------------------------


def test_get_user_roundtrips(store, cleanup_users):
    email = _unique_email()
    acct = store.register_user(email, "pw")
    cleanup_users.append(int(acct.id))

    got = store.get_user(acct.id)
    assert got is not None
    assert got.id == acct.id
    assert got.email == email
    assert got.api_key == acct.api_key


def test_update_user_flips_plan_active_admin(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw")
    cleanup_users.append(int(acct.id))

    updated = store.update_user(
        acct.id, is_active=False, plan="pro", is_admin=True
    )
    assert updated is not None
    assert updated.is_active is False
    assert updated.plan == "pro"
    assert updated.is_admin is True


def test_delete_user_soft_deletes(store, cleanup_users):
    acct = store.register_user(_unique_email(), "pw")
    cleanup_users.append(int(acct.id))

    assert store.delete_user(acct.id) is True
    assert store.get_user(acct.id) is None
    assert store.authenticate_user(acct.api_key) is None


def test_reset_password_invalidates_old_and_accepts_new(store, cleanup_users):
    email = _unique_email()
    acct = store.register_user(email, "old-pw")
    cleanup_users.append(int(acct.id))

    assert store.reset_password(acct.id, "new-pw") is True

    assert store.authenticate_with_password(email, "old-pw") is None
    got = store.authenticate_with_password(email, "new-pw")
    assert got is not None and got.id == acct.id


# ---------------------------------------------------------------------------
# Seed admin user (from seed_metadata.py) interop
# ---------------------------------------------------------------------------


def test_seed_admin_authenticates_through_crm_users_store(store):
    """The ``admin`` user (username=admin, is_admin=TRUE) comes from the
    CRM seed. It must authenticate via the new store too, proving the
    hermes↔CRM single-identity contract."""
    from hermes.crm.db import db_session
    from hermes.crm.models.users_db import UsersDb

    with db_session() as db:
        row = UsersDb(db).get_user_by_username("admin")
    assert row is not None and row.get("api_key")

    uid = store.authenticate_user(row["api_key"])
    assert uid == str(int(row["id"]))
    assert store.authenticate_admin(row["api_key"]) == str(int(row["id"]))


# ---------------------------------------------------------------------------
# main.py wiring — Phase 1c.2 scheme gate
# ---------------------------------------------------------------------------


def test_create_app_wires_crm_store_for_postgres_session_url():
    """Smoke-test the Phase 1c.2 selection ladder at the app-boot boundary:
    a ``postgresql+psycopg://`` ``session_store_url`` must route through
    :class:`CrmUsersAuthStore` when the CRM DB is reachable."""
    from hermes_cloud.app.config import AppConfig
    from hermes.crm.db import _resolve_dsn
    from hermes_cloud.app.crm_auth_store import CrmUsersAuthStore
    from hermes_cloud.app.main import create_app

    # Reuse the same DSN the CRM pool resolves — guarantees the scheme
    # match and that the downstream SA-based stores (config / session /
    # channel / profile) can also open a psycopg3 connection.
    dsn = _resolve_dsn().replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    app = create_app(AppConfig(session_store_url=dsn))
    assert isinstance(app.state.auth_store, CrmUsersAuthStore)


def test_create_app_falls_back_to_in_memory_when_no_session_url():
    """No ``session_store_url`` → the in-memory store, regardless of CRM
    reachability. Preserves unit-test isolation (callers using
    ``AppConfig()`` must not silently pick up shared PG state)."""
    from hermes_cloud.app.auth_store import InMemoryAuthStore
    from hermes_cloud.app.config import AppConfig
    from hermes_cloud.app.main import create_app

    app = create_app(AppConfig())
    assert isinstance(app.state.auth_store, InMemoryAuthStore)
