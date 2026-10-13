"""Tests for :class:`CrmProfileStore`.

Exercises the full ``ProfileStore`` protocol surface against the real
``hermes_crm_dev`` database. Same skip-if-unreachable policy as the
other CRM test modules.

Hygiene: every test creates its own user (via :class:`CrmUsersAuthStore`)
then soft-deletes it in teardown. ``profiles`` has no ``user_id`` column
(the link is ``profiles.id == users.username``, Charter v2 §简化原则), so
teardown deletes the profile row by username before marking the user
deleted — see the ``test_user`` fixture.
"""
from __future__ import annotations

import secrets

import pytest


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
    from hermes_cloud.app.crm_profile_store import CrmProfileStore

    return CrmProfileStore()


@pytest.fixture
def test_user():
    """Create a throw-away CRM user; yields the decimal-string uid.

    Cleans up at teardown by hard-deleting the user (profiles cascade).
    """
    from hermes.crm.db import db_session
    from hermes_cloud.app.crm_auth_store import CrmUsersAuthStore

    auth = CrmUsersAuthStore()
    email = f"pqa-{secrets.token_hex(4)}@example.com"
    acct = auth.register_user(email, "pw")
    uid = acct.id
    yield uid
    # Schema fact: profiles has no user_id column — the linkage is
    # profiles.id == users.username. Earlier teardown DELETE'd on
    # user_id (silently no-op), leaking orphan profile rows that the
    # boot loop in main.py then resurrected on the next cloud restart.
    with db_session() as db:
        db.execute(
            "DELETE FROM profiles WHERE id IN "
            "(SELECT username FROM users WHERE id = %s)", (int(uid),),
        )
        db.execute("UPDATE users SET deleted = TRUE WHERE id = %s", (int(uid),))


# ---------------------------------------------------------------------------
# ensure_default_profile
# ---------------------------------------------------------------------------


def test_ensure_default_creates_id_equals_username(store, test_user):
    # Charter v2 §简化原则 invariant: profile.id == users.username (a slug),
    # NOT the decimal users.id. profile.user_id still surfaces the decimal id
    # (via the _PROFILES_SELECT JOIN) for API compat.
    from hermes.crm.db import db_session

    with db_session() as db:
        username = db.fetchcolumn(
            "SELECT username FROM users WHERE id = %s", (int(test_user),)
        )
    p = store.ensure_default_profile(user_id=test_user)
    assert p.id == username  # invariant: default profile.id == users.username
    assert p.user_id == test_user
    assert p.is_default is True
    assert p.is_active is True
    assert p.slug  # slugged from DEFAULT_PROFILE_NAME


def test_ensure_default_is_idempotent(store, test_user):
    a = store.ensure_default_profile(user_id=test_user)
    b = store.ensure_default_profile(user_id=test_user)
    assert a.id == b.id


def test_ensure_default_rejects_non_integer_user_id(store):
    with pytest.raises(ValueError, match="integer"):
        store.ensure_default_profile(user_id="not-an-int")


# ---------------------------------------------------------------------------
# create_profile — Charter v2 §简化原则: removed (1 user = 1 profile)
# ---------------------------------------------------------------------------


def test_create_profile_is_removed(store, test_user):
    """Multi-profile-per-user is gone; create_profile is a hard-fail guard
    so any lingering caller surfaces instead of writing an unroutable row."""
    store.ensure_default_profile(user_id=test_user)
    with pytest.raises(NotImplementedError, match="1 user = 1 profile"):
        store.create_profile(user_id=test_user, name="secondary")


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------


def test_get_profile_roundtrips(store, test_user):
    p = store.ensure_default_profile(user_id=test_user)
    got = store.get_profile(p.id)
    assert got is not None and got.id == p.id and got.user_id == test_user


def test_get_default_profile(store, test_user):
    p = store.ensure_default_profile(user_id=test_user)
    got = store.get_default_profile(user_id=test_user)
    assert got is not None and got.id == p.id and got.is_default is True


def test_list_profiles_returns_only_the_default(store, test_user):
    # 1 user = 1 profile: list yields exactly the user's default.
    default = store.ensure_default_profile(user_id=test_user)
    rows = store.list_profiles(user_id=test_user)
    assert [r.id for r in rows] == [default.id]
    assert rows[0].is_default is True


def test_list_all_active_profiles_spans_users(store, test_user):
    store.ensure_default_profile(user_id=test_user)
    rows = store.list_all_active_profiles()
    # Our user must appear at least once (admin from seed may also be in there)
    assert any(r.user_id == test_user for r in rows)


# ---------------------------------------------------------------------------
# rename / update / set_default / delete
# ---------------------------------------------------------------------------


def test_rename_profile(store, test_user):
    p = store.ensure_default_profile(user_id=test_user)
    renamed = store.rename_profile(p.id, user_id=test_user, new_name="renamed")
    assert renamed is not None and renamed.name == "renamed"


def test_update_profile_slug_and_description(store, test_user):
    # The user's sole (default) profile is editable in place.
    p = store.ensure_default_profile(user_id=test_user)
    updated = store.update_profile(
        p.id, user_id=test_user, slug="second", description="desc"
    )
    assert updated is not None
    assert updated.slug == "second"
    assert updated.description == "desc"


def test_set_default_on_own_profile_is_idempotent(store, test_user):
    # 1 user = 1 profile: the sole profile is always the default; calling
    # set_default on it succeeds and leaves it default.
    d = store.ensure_default_profile(user_id=test_user)
    assert store.set_default(user_id=test_user, profile_id=d.id) is True
    new_default = store.get_default_profile(user_id=test_user)
    assert new_default is not None and new_default.id == d.id
    assert store.get_profile(d.id).is_default is True


def test_delete_default_profile_raises(store, test_user):
    from hermes_cloud.app.profile_store import CannotDeleteDefaultProfileError

    d = store.ensure_default_profile(user_id=test_user)
    with pytest.raises(CannotDeleteDefaultProfileError):
        store.delete_profile(d.id, user_id=test_user)


def test_user_mismatch_returns_none_or_false(store, test_user):
    p = store.ensure_default_profile(user_id=test_user)
    other_uid = str(int(test_user) + 999_999)
    assert store.rename_profile(p.id, user_id=other_uid, new_name="nope") is None
    assert (
        store.update_profile(p.id, user_id=other_uid, description="nope") is None
    )
    assert (
        store.set_default(user_id=other_uid, profile_id=p.id) is False
    )


# ---------------------------------------------------------------------------
# main.py wiring
# ---------------------------------------------------------------------------


def test_create_app_wires_crm_profile_store_for_postgres_url():
    """Phase 1c.3a: when ``session_store_url`` is a PG URL and the CRM
    DB is reachable, the profile store at boot is :class:`CrmProfileStore`."""
    from hermes_cloud.app.config import AppConfig
    from hermes.crm.db import _resolve_dsn
    from hermes_cloud.app.crm_profile_store import CrmProfileStore
    from hermes_cloud.app.main import create_app

    dsn = _resolve_dsn().replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    app = create_app(AppConfig(session_store_url=dsn))
    assert isinstance(app.state.profile_store, CrmProfileStore)
