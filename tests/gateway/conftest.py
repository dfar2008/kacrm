"""Shared fixtures for tests/gateway.

Provides ``db_session_clean`` — scoped DELETE of ``gateway_actions``
+ ``trust_decisions`` rows for the test's profile (``"42"``)
before/after each test, and ensures the spec's profile plus a
backing user exist so the FK on ``gateway_actions.profile_id`` is
satisfied. (Was originally TRUNCATE, but profile-scoped DELETE
avoids clobbering parallel tests that share the dev DB.)

Skips if no PG database is reachable (mirrors
``tests/agents/conftest.py``).
"""
from __future__ import annotations

import pytest


def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture
def db_session_clean():
    """Clean gateway_actions before & after; ensure profile '42' exists."""
    if not _crm_reachable():
        pytest.skip("hermes_crm_dev not reachable")

    from hermes.crm.db import db_session

    def _clean(db):
        db.execute(
            "DELETE FROM trust_decisions WHERE action_id IN "
            "(SELECT id FROM gateway_actions WHERE profile_id = '42')"
        )
        db.execute("DELETE FROM gateway_actions WHERE profile_id = '42'")
        db.execute("DELETE FROM trust_levels WHERE profile_id = '42'")
        # Charter §A2 — record_state lock rows for the test profile.
        db.execute("DELETE FROM record_writes WHERE profile_id = '42'")

    with db_session() as db:
        _clean(db)
        # Seed a sentinel user + profile id='42' so FKs hold. Idempotent.
        existing = db.fetchone("SELECT id FROM profiles WHERE id = '42'")
        if not existing:
            user_row = db.fetchone(
                "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
            )
            assert user_row, "admin user missing — run seed"
            db.execute(
                "INSERT INTO profiles (id, user_id, name, slug, "
                "is_default, is_active, description) "
                "VALUES ('42', %s, 'gw-test', 'gw-test', false, true, '') "
                "ON CONFLICT (id) DO NOTHING",
                (int(user_row["id"]),),
            )
        # Pin trust to L1 (REQUIRE_APPROVAL) for the test agents so the
        # crm.write executor never fires — the routing tests only care
        # about the gateway_actions row & the trust_decide call args.
        for agent in ("research", "intake", "agent:hermes"):
            db.execute(
                "INSERT INTO trust_levels (profile_id, agent_name, "
                "action_kind, level) VALUES ('42', %s, 'crm.write', 1) "
                "ON CONFLICT (profile_id, agent_name, action_kind) "
                "DO UPDATE SET level = 1",
                (agent,),
            )

    yield

    with db_session() as db:
        _clean(db)
