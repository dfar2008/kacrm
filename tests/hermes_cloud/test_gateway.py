"""Tests for :mod:`hermes_cloud.app.gateway.dispatcher`.

Exercises the submit → approve/reject → revert lifecycle with a
real CRM write executor against the dev PG.
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


@pytest.fixture(scope="module")
def accounts_module_id() -> int:
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM collections WHERE name = 'accounts' LIMIT 1"
        )
    assert row, "accounts collection missing — run seed"
    return int(row["id"])


def _cleanup_action(action_id: str) -> None:
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM trust_decisions WHERE action_id = %s",
            (action_id,),
        )
        db.execute(
            "DELETE FROM gateway_actions WHERE id = %s",
            (action_id,),
        )


def _cleanup_account(record_id: int) -> None:
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM accounts WHERE accounts_id = %s",
            (record_id,),
        )


# ---------------------------------------------------------------------------
# Trust Engine gating
# ---------------------------------------------------------------------------


def test_submit_default_auto_executes_internal_kind(
    profile_id, admin_id, accounts_module_id,
):
    """``crm.write`` is in dispatcher ``_FORCE_AUTO_EXECUTE_KINDS`` —
    even at the default L1 trust level (which the engine would otherwise
    map to REQUIRE_APPROVAL) the dispatcher runs the executor right away
    and the row lands ``executed`` with a revert_token. Only egress
    kinds (``email.send`` & co.) still queue for human review."""
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.action_gateway.dispatcher import submit

    action = submit(
        profile_id=profile_id,
        agent_name="field_extractor",
        kind="crm.write",
        target={"module": "accounts"},
        params={
            "module_id": accounts_module_id,
            "op": "create",
            "data": {"accounts_name": f"GW-Auto-{secrets.token_hex(4)}"},
        },
        user_id=admin_id,
    )
    try:
        assert action["state"] == "executed"
        assert action["revert_token"]["op"] == "delete"
        assert action["revert_token"]["record_id"] > 0
    finally:
        if action.get("revert_token", {}).get("record_id"):
            _cleanup_account(action["revert_token"]["record_id"])
        _cleanup_action(str(action["id"]))


def test_submit_l5_auto_executes(profile_id, admin_id, accounts_module_id):
    """Trust level 5 should auto-execute the action."""
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.action_gateway.dispatcher import submit
    from hermes.trust.engine import set_level

    agent = f"probe_{secrets.token_hex(4)}"
    set_level(profile_id, agent, "crm.write", 5)
    name = f"GW-L5-{secrets.token_hex(4)}"

    action = submit(
        profile_id=profile_id,
        agent_name=agent,
        kind="crm.write",
        params={
            "module_id": accounts_module_id,
            "op": "create",
            "data": {"accounts_name": name, "owner_by": admin_id},
        },
        user_id=admin_id,
    )
    try:
        assert action["state"] == "executed"
        assert action["executed_at"] is not None
        assert action["revert_token"] is not None
        assert action["revert_token"]["op"] == "delete"
        record_id = action["revert_token"]["record_id"]
        assert record_id > 0
    finally:
        # Clean up the created account and the action
        if action.get("revert_token", {}).get("record_id"):
            _cleanup_account(action["revert_token"]["record_id"])
        _cleanup_action(str(action["id"]))
        from hermes.crm.db import db_session

        with db_session() as db:
            db.execute(
                "DELETE FROM trust_levels WHERE profile_id = %s AND agent_name = %s",
                (profile_id, agent),
            )


def test_submit_l0_blocks(profile_id, admin_id, accounts_module_id):
    """Trust level 0 should block and raise."""
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.action_gateway.dispatcher import ActionBlockedError, submit
    from hermes.trust.engine import set_level

    agent = f"probe_{secrets.token_hex(4)}"
    set_level(profile_id, agent, "crm.write", 0)

    with pytest.raises(ActionBlockedError):
        submit(
            profile_id=profile_id,
            agent_name=agent,
            kind="crm.write",
            params={
                "module_id": accounts_module_id,
                "op": "create",
                "data": {"accounts_name": "should-be-blocked"},
            },
            user_id=admin_id,
        )

    # Clean up the blocked action row + trust level
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            """DELETE FROM trust_decisions
               WHERE action_id IN (
                   SELECT id FROM gateway_actions
                   WHERE profile_id = %s AND agent_name = %s
               )""",
            (profile_id, agent),
        )
        db.execute(
            "DELETE FROM gateway_actions WHERE profile_id = %s AND agent_name = %s",
            (profile_id, agent),
        )
        db.execute(
            "DELETE FROM trust_levels WHERE profile_id = %s AND agent_name = %s",
            (profile_id, agent),
        )


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------
#
# The approve/reject lifecycle for ``crm.write`` (and the other internal
# kinds in ``_FORCE_AUTO_EXECUTE_KINDS``) was dropped — those actions
# now run on submit, there is nothing to approve. The egress kinds that
# still queue for review have their own coverage under
# ``tests/hermes_cloud/test_gateway_origin_channel.py`` and
# ``test_gateway_pending_notifier.py`` (parameterized on ``email.send``).


def test_revert_soft_deletes_created_record(
    profile_id, admin_id, accounts_module_id
):
    """Reverting an auto-executed crm.write should soft-delete the row."""
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.action_gateway.dispatcher import revert, submit

    name = f"GW-Revert-{secrets.token_hex(4)}"
    action = submit(
        profile_id=profile_id,
        agent_name="field_extractor",
        kind="crm.write",
        params={
            "module_id": accounts_module_id,
            "op": "create",
            "data": {"accounts_name": name, "owner_by": admin_id},
        },
        user_id=admin_id,
    )
    assert action["state"] == "executed"
    record_id = action["revert_token"]["record_id"]

    reverted = revert(str(action["id"]), admin_id)
    try:
        assert reverted["state"] == "reverted"
        assert reverted["reverted_at"] is not None

        # Verify the record is soft-deleted
        from hermes.crm.db import db_session

        with db_session() as db:
            row = db.fetchone(
                "SELECT deleted FROM accounts WHERE accounts_id = %s",
                (record_id,),
            )
        assert row["deleted"] is True
    finally:
        _cleanup_account(record_id)
        _cleanup_action(str(action["id"]))
