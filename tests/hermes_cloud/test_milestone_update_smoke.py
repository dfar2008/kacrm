"""End-to-end smoke: agent → crm_write → potentials.milestone update.

Verifies PR-1 is non-breaking against the four real ways milestone gets
written today (SPA chatbox, copilot, WeChat skill, Feishu skill). All
four converge on ``hermes.tools.crm.dispatch.dispatch(tool_name='crm_write',
action='update', module='potentials', data={milestone: 'negotiation'})``,
so exercising that single entry point covers them all.

The new pipeline_milestones table is **not** yet enforced as a FK on
``potentials.milestone`` (that's PR-2), so this smoke confirms the
existing free-text write path still works exactly as before, and that
the milestone value also resolves cleanly against the new dictionary
table (forward-compatibility check).
"""
from __future__ import annotations

import secrets

import pytest

pytestmark = pytest.mark.requires_real_pg


@pytest.fixture(scope="module")
def admin_user_id() -> int:
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone("SELECT id FROM users WHERE username = 'admin' LIMIT 1")
    assert row, "admin user missing — run seed"
    return int(row["id"])


@pytest.fixture
def opp_id(admin_user_id: int) -> int:
    """Create a fresh potentials row and return its id."""
    from hermes.crm.db import db_session
    suffix = secrets.token_hex(4)
    with db_session() as db:
        row = db.fetchone(
            """
            INSERT INTO potentials (potentials_name, owner_by, milestone)
            VALUES (%s, %s, %s)
            RETURNING potentials_id
            """,
            (f"smoke_{suffix}", admin_user_id, "initial_contact"),
        )
    return int(row["potentials_id"])


def test_dispatch_updates_milestone_via_crm_write(
    admin_user_id: int, opp_id: int
) -> None:
    """The path used by SPA chatbox + copilot + skill tool calls."""
    from hermes.tools.crm.dispatch import dispatch

    result = dispatch(
        tool_name="crm_write",
        arguments={
            "module": "potentials",
            "action": "update",
            "id": opp_id,
            "data": {"milestone": "negotiation"},
        },
        user_id=admin_user_id,
        source="manual",
    )
    assert result.get("success") is True
    assert int(result.get("id")) == opp_id

    from hermes.crm.db import db_session
    with db_session() as db:
        cur = db.fetchone(
            "SELECT milestone FROM potentials WHERE potentials_id = %s", (opp_id,)
        )
    assert cur["milestone"] == "negotiation"


def test_milestone_value_resolves_against_dictionary(opp_id: int) -> None:
    """Forward-compat check: the value we just wrote matches a row in the
    new pipeline_milestones table (so when PR-2 adds the FK, this row
    won't suddenly be invalid)."""
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone(
            """
            SELECT p.milestone, pm.kind, pm.label
              FROM potentials p
              JOIN pipeline_milestones pm ON pm.name = p.milestone
             WHERE p.potentials_id = %s
            """,
            (opp_id,),
        )
    assert row is not None, "milestone value 'initial_contact' should join cleanly"
    assert row["kind"] == "progressing"
    assert row["label"] == "Initial Contact"


def test_setting_milestone_to_won_currently_rejected_by_module_fields_options(
    admin_user_id: int, opp_id: int
) -> None:
    """**Pre-existing drift, NOT introduced by PR-1.**

    The seed for ``module_fields.options`` on potentials.milestone lists
    only the 5 progressing stages (initial_contact … closing) — it omits
    the terminal ``won`` / ``lost`` values. Save.set_module's
    ``_validate_select_options`` therefore rejects any attempt by an
    agent / SPA / channel skill to mark a deal won or lost via
    ``crm_write``.

    This documents the gap (agents literally cannot close deals through
    the normal write path today) and locks the behaviour so PR-3 + the
    seed_metadata fix (governance plan task 3) get caught when they
    flip it. After that PR this test should be inverted.
    """
    from hermes.crm.save import FieldValidationException
    from hermes.tools.crm.dispatch import dispatch
    with pytest.raises(FieldValidationException) as exc:
        dispatch(
            tool_name="crm_write",
            arguments={
                "module": "potentials",
                "action": "update",
                "id": opp_id,
                "data": {"milestone": "won"},
            },
            user_id=admin_user_id,
            source="manual",
        )
    assert exc.value.field_name == "milestone"
    assert "won" in str(exc.value.message) or "won" in str(exc.value)
