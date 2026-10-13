"""Tests for :mod:`hermes.trust.engine`.

Covers the trust level CRUD and the decide() matrix. DB-backed tests
are gated behind ``_crm_reachable`` like the other CRM test suites.
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


@pytest.fixture(scope="module")
def profile_id() -> str:
    """Use a real profile id from the seed."""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone("SELECT id FROM profiles LIMIT 1")
    assert row, "profiles table empty — run seed"
    return str(row["id"])


@pytest.fixture(scope="module")
def admin_id() -> int:
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row, "admin user missing — run seed"
    return int(row["id"])


def _cleanup_trust(profile_id: str, agent_name: str) -> None:
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM trust_levels WHERE profile_id = %s AND agent_name = %s",
            (profile_id, agent_name),
        )


def test_get_level_returns_default_when_no_row(profile_id):
    from hermes.trust.engine import DEFAULT_LEVEL, get_level

    agent = f"probe_{secrets.token_hex(4)}"
    level = get_level(profile_id, agent, "crm.write")
    assert level == DEFAULT_LEVEL


def test_set_and_get_level(profile_id, admin_id):
    from hermes.trust.engine import get_level, set_level

    agent = f"probe_{secrets.token_hex(4)}"
    try:
        set_level(profile_id, agent, "crm.write", 3, updated_by=admin_id)
        assert get_level(profile_id, agent, "crm.write") == 3

        # Upsert: overwrite to 0
        set_level(profile_id, agent, "crm.write", 0, rationale="incident")
        assert get_level(profile_id, agent, "crm.write") == 0
    finally:
        _cleanup_trust(profile_id, agent)


def test_set_level_rejects_out_of_range(profile_id):
    from hermes.trust.engine import set_level

    with pytest.raises(ValueError, match="0–5"):
        set_level(profile_id, "x", "y", 6)
    with pytest.raises(ValueError, match="0–5"):
        set_level(profile_id, "x", "y", -1)


def test_decide_l0_blocks(profile_id):
    from hermes.trust.engine import Decision, decide, set_level

    agent = f"probe_{secrets.token_hex(4)}"
    try:
        set_level(profile_id, agent, "crm.write", 0)
        v = decide(profile_id, agent, "crm.write")
        assert v.decision == Decision.BLOCK
        assert v.level == 0
    finally:
        _cleanup_trust(profile_id, agent)


def test_decide_l1_requires_approval(profile_id):
    from hermes.trust.engine import Decision, decide

    agent = f"probe_{secrets.token_hex(4)}"
    v = decide(profile_id, agent, "crm.write")
    assert v.decision == Decision.REQUIRE_APPROVAL
    assert v.level == 1


def test_decide_l5_full_auto(profile_id):
    from hermes.trust.engine import Decision, decide, set_level

    agent = f"probe_{secrets.token_hex(4)}"
    try:
        set_level(profile_id, agent, "crm.write", 5)
        v = decide(profile_id, agent, "crm.write", risk="critical")
        assert v.decision == Decision.EXECUTE
    finally:
        _cleanup_trust(profile_id, agent)


def test_decide_l2_risk_gating(profile_id):
    from hermes.trust.engine import Decision, decide, set_level

    agent = f"probe_{secrets.token_hex(4)}"
    try:
        set_level(profile_id, agent, "crm.write", 2)
        assert decide(profile_id, agent, "crm.write", risk="low").decision == Decision.EXECUTE
        assert decide(profile_id, agent, "crm.write", risk="medium").decision == Decision.REQUIRE_APPROVAL
    finally:
        _cleanup_trust(profile_id, agent)


def test_decide_l3_risk_gating(profile_id):
    from hermes.trust.engine import Decision, decide, set_level

    agent = f"probe_{secrets.token_hex(4)}"
    try:
        set_level(profile_id, agent, "crm.write", 3)
        assert decide(profile_id, agent, "crm.write", risk="low").decision == Decision.EXECUTE
        assert decide(profile_id, agent, "crm.write", risk="medium").decision == Decision.EXECUTE
        assert decide(profile_id, agent, "crm.write", risk="high").decision == Decision.REQUIRE_APPROVAL
    finally:
        _cleanup_trust(profile_id, agent)


def test_decide_l4_risk_gating(profile_id):
    from hermes.trust.engine import Decision, decide, set_level

    agent = f"probe_{secrets.token_hex(4)}"
    try:
        set_level(profile_id, agent, "crm.write", 4)
        assert decide(profile_id, agent, "crm.write", risk="high").decision == Decision.EXECUTE
        assert decide(profile_id, agent, "crm.write", risk="critical").decision == Decision.REQUIRE_APPROVAL
    finally:
        _cleanup_trust(profile_id, agent)
