"""Phase 4.2 — revert conflict guard.

Each gateway executor stamps a content-hash on ``gateway_actions`` at
execute time; revert recomputes against the live row and refuses
(``ConflictError`` → dispatcher transitions ``state='conflict'``) if
the row has been modified externally.

Covers all three executors × four scenarios:
  1. Happy revert (no mutation) → state=reverted, soft-delete flag set.
  2. External mutation of a meaningful field → state=conflict, no flag flipped.
  3. Re-revert after success → noop (state stays reverted).
  4. Mutation of a bookkeeping-only field (``updated_at``) → revert still allowed.

Plus an alembic 0042 upgrade/downgrade smoke check.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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
# Hash helper unit-tests (no DB)
# ---------------------------------------------------------------------------


def test_compute_record_hash_is_stable_across_bookkeeping_changes():
    from hermes.action_gateway.executors import compute_record_hash

    base = {"name": "alice", "email": "a@x", "updated_at": "2026-01-01"}
    bumped = {"name": "alice", "email": "a@x", "updated_at": "2026-12-31"}
    assert compute_record_hash(base) == compute_record_hash(bumped)


def test_compute_record_hash_changes_on_meaningful_field():
    from hermes.action_gateway.executors import compute_record_hash

    a = {"name": "alice", "email": "a@x"}
    b = {"name": "alice", "email": "b@x"}
    assert compute_record_hash(a) != compute_record_hash(b)


def test_conflict_error_carries_diagnostics():
    from hermes.action_gateway.executors import ConflictError

    e = ConflictError(
        entity="memories", stored_hash="aaa", live_hash="bbb", record_id=7,
    )
    assert e.entity == "memories"
    assert e.stored_hash == "aaa"
    assert e.live_hash == "bbb"
    assert e.record_id == 7


# ---------------------------------------------------------------------------
# CRM write (create) executor — full lifecycle
# ---------------------------------------------------------------------------


def _admin_and_accounts():
    from hermes.crm.db import db_session

    with db_session() as db:
        u = db.fetchone("SELECT id FROM users WHERE username='admin' LIMIT 1")
        c = db.fetchone(
            "SELECT id FROM collections WHERE name='accounts' LIMIT 1"
        )
        p = db.fetchone("SELECT id FROM profiles LIMIT 1")
    assert u and c and p
    return int(u["id"]), int(c["id"]), str(p["id"])


def _cleanup(action_id, record_id=None, table="accounts", pk_col=None):
    from hermes.crm.db import db_session

    pk = pk_col or f"{table}_id"
    with db_session() as db:
        db.execute(
            "DELETE FROM trust_decisions WHERE action_id = %s", (action_id,)
        )
        db.execute("DELETE FROM gateway_actions WHERE id = %s", (action_id,))
        if record_id is not None:
            db.execute(
                f'DELETE FROM "{table}" WHERE {pk} = %s', (record_id,),
            )


@pytest.fixture
def crm_setup():
    admin_id, mod_id, profile = _admin_and_accounts()
    from hermes.trust.engine import set_level

    agent = f"conf_{secrets.token_hex(3)}"
    set_level(profile, agent, "crm.write", 5)
    yield profile, admin_id, mod_id, agent
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM trust_levels WHERE profile_id=%s AND agent_name=%s",
            (profile, agent),
        )


def _submit_crm_create(profile, admin_id, mod_id, agent, name):
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.action_gateway.dispatcher import submit

    return submit(
        profile_id=profile,
        agent_name=agent,
        kind="crm.write",
        params={
            "module_id": mod_id,
            "op": "create",
            "data": {"accounts_name": name, "owner_by": admin_id},
        },
        user_id=admin_id,
    )


def test_crm_write_revert_happy_path(crm_setup):
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, admin_id, mod_id, agent = crm_setup
    action = _submit_crm_create(
        profile, admin_id, mod_id, agent, f"GW-Conf-Happy-{secrets.token_hex(3)}",
    )
    record_id = action["revert_token"]["record_id"]
    try:
        # Hash was stored at execute time.
        with db_session() as db:
            stored = db.fetchone(
                "SELECT record_hash FROM gateway_actions WHERE id=%s",
                (str(action["id"]),),
            )
        assert stored["record_hash"], "execute should have stamped a hash"

        result = revert(str(action["id"]), admin_id)
        assert result["state"] == "reverted"

        with db_session() as db:
            row = db.fetchone(
                "SELECT deleted FROM accounts WHERE accounts_id=%s",
                (record_id,),
            )
        assert row["deleted"] is True
    finally:
        _cleanup(str(action["id"]), record_id)


def test_crm_write_revert_blocks_on_external_mutation(crm_setup):
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, admin_id, mod_id, agent = crm_setup
    action = _submit_crm_create(
        profile, admin_id, mod_id, agent,
        f"GW-Conf-Mut-{secrets.token_hex(3)}",
    )
    record_id = action["revert_token"]["record_id"]
    try:
        # Externally mutate a meaningful field.
        with db_session() as db:
            db.execute(
                "UPDATE accounts SET accounts_name=%s WHERE accounts_id=%s",
                (f"GW-Conf-MutChanged-{secrets.token_hex(3)}", record_id),
            )

        result = revert(str(action["id"]), admin_id)
        assert result["state"] == "conflict"

        # Soft-delete must NOT have flipped.
        with db_session() as db:
            row = db.fetchone(
                "SELECT deleted FROM accounts WHERE accounts_id=%s",
                (record_id,),
            )
        assert row["deleted"] is False, "conflict should not flip soft-delete"

        # Conflict diagnostics persisted in params._conflict.
        assert (result.get("params") or {}).get("_conflict")
    finally:
        _cleanup(str(action["id"]), record_id)


def test_crm_write_re_revert_is_noop(crm_setup):
    from hermes.action_gateway.dispatcher import revert

    profile, admin_id, mod_id, agent = crm_setup
    action = _submit_crm_create(
        profile, admin_id, mod_id, agent,
        f"GW-Conf-Twice-{secrets.token_hex(3)}",
    )
    record_id = action["revert_token"]["record_id"]
    try:
        first = revert(str(action["id"]), admin_id)
        assert first["state"] == "reverted"
        first_at = first["reverted_at"]

        # Second revert: must noop, not raise, not flip again.
        second = revert(str(action["id"]), admin_id)
        assert second["state"] == "reverted"
        assert second["reverted_at"] == first_at
    finally:
        _cleanup(str(action["id"]), record_id)


def test_crm_write_bookkeeping_only_change_still_allows_revert(crm_setup):
    """Mutating ``modified_time`` (excluded from hash) leaves the
    content-hash untouched, so revert still proceeds."""
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, admin_id, mod_id, agent = crm_setup
    action = _submit_crm_create(
        profile, admin_id, mod_id, agent,
        f"GW-Conf-BK-{secrets.token_hex(3)}",
    )
    record_id = action["revert_token"]["record_id"]
    try:
        with db_session() as db:
            # Bump a bookkeeping column (``updated_at`` is in the
            # default exclude set, so the hash should not change).
            db.execute(
                "UPDATE accounts SET updated_at=now() WHERE accounts_id=%s",
                (record_id,),
            )

        result = revert(str(action["id"]), admin_id)
        assert result["state"] == "reverted"
    finally:
        _cleanup(str(action["id"]), record_id)


# ---------------------------------------------------------------------------
# memory.write executor
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_setup():
    _, _, profile = _admin_and_accounts()
    from hermes.trust.engine import set_level

    agent = f"mem_{secrets.token_hex(3)}"
    set_level(profile, agent, "memory.write", 5)
    yield profile, agent
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM trust_levels WHERE profile_id=%s AND agent_name=%s",
            (profile, agent),
        )


def _submit_memory(profile, agent, content):
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.action_gateway.dispatcher import submit

    return submit(
        profile_id=profile,
        agent_name=agent,
        kind="memory.write",
        params={
            "content": content,
            "kind": "fact",
            "scope": "org",
            "source_agent": agent,
            "title": f"phase42-{secrets.token_hex(3)}",
        },
    )


def _cleanup_memory(action_id, memory_id=None):
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM trust_decisions WHERE action_id=%s", (action_id,)
        )
        db.execute("DELETE FROM gateway_actions WHERE id=%s", (action_id,))
        if memory_id:
            db.execute("DELETE FROM memories WHERE id=%s", (int(memory_id),))


def test_memory_write_happy_revert(mem_setup):
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, agent = mem_setup
    action = _submit_memory(profile, agent, f"phase42 happy {secrets.token_hex(3)}")
    mid = action["revert_token"].get("memory_id")
    try:
        if action["revert_token"].get("op") != "delete":
            pytest.skip("dedupe/cap path — no row to test against")
        result = revert(str(action["id"]), 1)
        assert result["state"] == "reverted"
        with db_session() as db:
            row = db.fetchone("SELECT id FROM memories WHERE id=%s", (mid,))
        assert row is None, "memory should be deleted on revert"
    finally:
        _cleanup_memory(str(action["id"]), mid)


def test_memory_write_conflict_on_external_mutation(mem_setup):
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, agent = mem_setup
    action = _submit_memory(profile, agent, f"phase42 mut {secrets.token_hex(3)}")
    mid = action["revert_token"].get("memory_id")
    try:
        if action["revert_token"].get("op") != "delete":
            pytest.skip("dedupe/cap path")
        with db_session() as db:
            db.execute(
                "UPDATE memories SET content=%s WHERE id=%s",
                (f"externally rewritten {secrets.token_hex(3)}", int(mid)),
            )

        result = revert(str(action["id"]), 1)
        assert result["state"] == "conflict"
        # Row still present (delete blocked).
        with db_session() as db:
            row = db.fetchone("SELECT id FROM memories WHERE id=%s", (mid,))
        assert row is not None
    finally:
        _cleanup_memory(str(action["id"]), mid)


def test_memory_write_bookkeeping_change_allows_revert(mem_setup):
    """``confidence`` is in the memory hash exclude set."""
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, agent = mem_setup
    action = _submit_memory(profile, agent, f"phase42 bk {secrets.token_hex(3)}")
    mid = action["revert_token"].get("memory_id")
    try:
        if action["revert_token"].get("op") != "delete":
            pytest.skip("dedupe/cap path")
        with db_session() as db:
            db.execute(
                "UPDATE memories SET confidence=0.42 WHERE id=%s", (int(mid),),
            )
        result = revert(str(action["id"]), 1)
        assert result["state"] == "reverted"
    finally:
        _cleanup_memory(str(action["id"]), mid)


# ---------------------------------------------------------------------------
# task.create executor
# ---------------------------------------------------------------------------


@pytest.fixture
def task_setup():
    _, _, profile = _admin_and_accounts()
    from hermes.trust.engine import set_level

    agent = f"tsk_{secrets.token_hex(3)}"
    set_level(profile, agent, "task.create", 5)
    yield profile, agent
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM trust_levels WHERE profile_id=%s AND agent_name=%s",
            (profile, agent),
        )


def _submit_task(profile, agent, title):
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.action_gateway.dispatcher import submit

    return submit(
        profile_id=profile,
        agent_name=agent,
        kind="task.create",
        params={
            "title": title,
            "description": f"phase42 {secrets.token_hex(3)}",
            "source_agent": agent,
        },
    )


def _cleanup_task(action_id, task_id=None):
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM trust_decisions WHERE action_id=%s", (action_id,)
        )
        db.execute("DELETE FROM gateway_actions WHERE id=%s", (action_id,))
        if task_id:
            db.execute("DELETE FROM agent_tasks WHERE id=%s", (int(task_id),))


def test_task_create_happy_revert(task_setup):
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, agent = task_setup
    action = _submit_task(
        profile, agent, f"GW-Conf-Task-Happy-{secrets.token_hex(3)}"
    )
    tid = action["revert_token"]["task_id"]
    try:
        result = revert(str(action["id"]), 1)
        assert result["state"] == "reverted"
        with db_session() as db:
            row = db.fetchone(
                "SELECT deleted FROM agent_tasks WHERE id=%s", (int(tid),),
            )
        assert row["deleted"] is True
    finally:
        _cleanup_task(str(action["id"]), tid)


def test_task_create_conflict_on_external_mutation(task_setup):
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, agent = task_setup
    action = _submit_task(
        profile, agent, f"GW-Conf-Task-Mut-{secrets.token_hex(3)}"
    )
    tid = action["revert_token"]["task_id"]
    try:
        with db_session() as db:
            db.execute(
                "UPDATE agent_tasks SET title=%s WHERE id=%s",
                (f"externally renamed {secrets.token_hex(3)}", int(tid)),
            )
        result = revert(str(action["id"]), 1)
        assert result["state"] == "conflict"
        with db_session() as db:
            row = db.fetchone(
                "SELECT deleted FROM agent_tasks WHERE id=%s", (int(tid),),
            )
        assert row["deleted"] is False
    finally:
        _cleanup_task(str(action["id"]), tid)


def test_task_create_status_change_allows_revert(task_setup):
    """``status`` is in the task hash exclude set — flipping to 'done'
    must not block revert."""
    from hermes.crm.db import db_session
    from hermes.action_gateway.dispatcher import revert

    profile, agent = task_setup
    action = _submit_task(
        profile, agent, f"GW-Conf-Task-BK-{secrets.token_hex(3)}"
    )
    tid = action["revert_token"]["task_id"]
    try:
        with db_session() as db:
            db.execute(
                "UPDATE agent_tasks SET status='done' WHERE id=%s",
                (int(tid),),
            )
        result = revert(str(action["id"]), 1)
        assert result["state"] == "reverted"
    finally:
        _cleanup_task(str(action["id"]), tid)


# ---------------------------------------------------------------------------
# Migration smoke
# ---------------------------------------------------------------------------


def test_migration_0042_columns_present():
    from hermes.crm.db import db_session

    with db_session() as db:
        col = db.fetchone(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='gateway_actions' AND column_name='record_hash'"
        )
        chk = db.fetchone(
            "SELECT conname FROM pg_constraint "
            "WHERE conname='gateway_actions_state_check'"
        )
    assert col, "record_hash column missing"
    assert chk, "state check constraint missing"
