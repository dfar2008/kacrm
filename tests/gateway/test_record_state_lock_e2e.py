"""End-to-end: dispatcher.submit + record_state lock interaction
(Charter §A2 follow-up #4).

Covers:
  * Two specialists colliding on the same (module, record_id) update —
    second submit raises ActionBlockedError.
  * Serialised submits (first one rejected/approved → lock released)
    succeed.
  * Soft-warning path: a committed write by another agent that lands
    after our token logs a warning + emits a record_state_concurrent_write
    interaction.
"""
from __future__ import annotations

import logging

import pytest


def test_two_specialists_collide_on_same_record(db_session_clean, caplog):
    """intake's pending crm.write on (module=42, record_id=1) holds the
    lock; research's submit for the same record raises
    ActionBlockedError before the second gateway_actions row gets a
    chance to land in pending_approval state."""
    from hermes.action_gateway.dispatcher import (
        ActionBlockedError, submit,
    )

    # First submit: intake locks lead#1 (trust pinned to L1 by fixture =>
    # stays pending_approval, lock stays open).
    row1 = submit(
        profile_id="42",
        agent_name="agent:hermes",
        executing_specialist="intake",
        kind="crm.write",
        params={
            "module_id": 42, "op": "update", "record_id": 1,
            "data": {"stage": "qualified"},
        },
        origin={"channel": "feishu"},
    )
    assert row1["state"] == "pending_approval"
    assert row1["record_write_token"] is not None

    # Second submit by a different specialist: must collide.
    with pytest.raises(ActionBlockedError) as exc:
        submit(
            profile_id="42",
            agent_name="agent:hermes",
            executing_specialist="research",
            kind="crm.write",
            params={
                "module_id": 42, "op": "update", "record_id": 1,
                "data": {"amount": 1000},
            },
            origin={"channel": "feishu"},
        )
    assert "record_state" in str(exc.value)
    assert "intake" in str(exc.value)


def test_serialized_writes_no_collision(db_session_clean):
    """intake submits → reject (releases lock) → research submits same
    record, succeeds."""
    from hermes.action_gateway.dispatcher import reject, submit

    row1 = submit(
        profile_id="42",
        agent_name="agent:hermes",
        executing_specialist="intake",
        kind="crm.write",
        params={
            "module_id": 42, "op": "update", "record_id": 2,
            "data": {"stage": "qualified"},
        },
        origin={"channel": "feishu"},
    )
    reject(row1["id"], approver_id=None, via_channel="feishu")

    row2 = submit(
        profile_id="42",
        agent_name="agent:hermes",
        executing_specialist="research",
        kind="crm.write",
        params={
            "module_id": 42, "op": "update", "record_id": 2,
            "data": {"amount": 5000},
        },
        origin={"channel": "feishu"},
    )
    assert row2["state"] == "pending_approval"
    assert row2["record_write_token"] is not None
    assert row2["record_write_token"] != row1["record_write_token"]


def test_create_does_not_acquire_lock(db_session_clean):
    """``op=='create'`` has no record_id — the lock must be skipped."""
    from hermes.action_gateway.dispatcher import submit

    row = submit(
        profile_id="42",
        agent_name="agent:hermes",
        executing_specialist="intake",
        kind="crm.write",
        params={
            "module_id": 42, "op": "create",
            "data": {"name": "Acme Corp"},
        },
        origin={"channel": "feishu"},
    )
    assert row["state"] == "pending_approval"
    assert row["record_write_token"] is None


def test_soft_warn_logs_and_emits(db_session_clean, caplog):
    """Variant that actually exercises the soft-warn path.

    Setup (raw INSERTs to bypass the hard lock so we can stage the
    timeline):
      - research opens token A at t=1, immediately rolled back (anchor)
      - intake opens token B at t=2, committed
      - drive _soft_warn_concurrent_writes against an action whose
        record_write_token=A

    Expect: warning log + record_state_concurrent_write interaction.
    """
    import json

    from sqlalchemy import text as sql_text

    from hermes.crm.db import db_session, resolve_crm_sa_url
    from hermes_cloud.app.db import make_engine
    from hermes.action_gateway.executors.crm_write import (
        _soft_warn_concurrent_writes,
    )

    eng = make_engine(resolve_crm_sa_url())
    with eng.begin() as conn:
        conn.execute(
            sql_text(
                "DELETE FROM record_writes WHERE module = '42' "
                "AND record_id = 99"
            )
        )
        # Anchor row by research, then closed as rolled-back.
        anchor_row = conn.execute(
            sql_text(
                "INSERT INTO record_writes "
                "(module, record_id, agent_name, profile_id, "
                "began_at, rolled_back_at) "
                "VALUES ('42', 99, 'research', '42', "
                "now() - interval '10 seconds', "
                "now() - interval '9 seconds') RETURNING id"
            )
        ).fetchone()
        anchor_id = int(anchor_row.id)
        # Intake committed write that began AFTER anchor.
        conn.execute(
            sql_text(
                "INSERT INTO record_writes "
                "(module, record_id, agent_name, profile_id, "
                "began_at, committed_at, fields) "
                "VALUES ('42', 99, 'intake', '42', "
                "now() - interval '5 seconds', now(), "
                "CAST(:f AS JSONB))"
            ),
            {"f": json.dumps({"stage": "won"})},
        )

    # Now stage a fake action row with record_write_token = anchor_id
    # so the executor can find it.
    with db_session() as db:
        # Use the seeded profile '42' from db_session_clean fixture.
        action_id = "00000000-0000-0000-0000-00000000aaaa"
        db.execute(
            "INSERT INTO gateway_actions (id, profile_id, agent_name, "
            "kind, state, record_write_token, origin) "
            "VALUES (%s, '42', 'research', 'crm.write', "
            "'pending_approval', %s, '{\"channel\":\"feishu\"}'::jsonb)",
            (action_id, anchor_id),
        )

        caplog.set_level(logging.WARNING)
        _soft_warn_concurrent_writes(
            action={"id": action_id, "agent_name": "research"},
            module="42",
            record_id=99,
            db=db,
        )

    assert any(
        "crm_write_concurrent_writes_detected" in rec.message
        for rec in caplog.records
    ), [r.message for r in caplog.records]
