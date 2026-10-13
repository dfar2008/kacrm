"""E2E privacy isolation: a manager's decision conversation about user X
must not surface in:
  - X's record timeline (subjects @> [{"module":"customers","id":...}])
  - team_activity feed (kind whitelist excludes copilot_message)
  - X's own view (X is not the actor)
  - other employees' views (visibility=self_only)

But manager themselves MUST see their own thread (Copilot panel
共享流). Spec §7.4 mandatory test.

Requires real PG (HERMES_TESTS_REAL_PG=1).
"""
from __future__ import annotations
import json
import uuid

import pytest

pytestmark = pytest.mark.requires_real_pg


@pytest.fixture
def db_session():
    from hermes.crm.db import db_session as _ds
    with _ds() as db:
        yield db


@pytest.fixture
def setup_manager_thread(db_session):
    """Manager (id=test_manager_id) discusses X (id=test_subject_id) in a
    decision thread. Yields (thread_id, [user_msg_id, asst_msg_id],
    manager_id, subject_id). Cleans up afterward.
    """
    from hermes_cloud.app.decisions.store import (
        DecisionMessage, insert_message,
    )
    db = db_session
    # Use random ids to avoid collision with prior runs.
    manager_id = 9_900_000 + int(uuid.uuid4().int % 9999)
    subject_id = 9_910_000 + int(uuid.uuid4().int % 9999)
    thread_id = str(uuid.uuid4())

    user_msg_id = insert_message(db, DecisionMessage(
        thread_id=thread_id, manager_id=manager_id, role="user",
        text="X 本周接受率 0%，怎么回事", source_surface="spa",
    ))
    asst_msg_id = insert_message(db, DecisionMessage(
        thread_id=thread_id, manager_id=manager_id, role="assistant",
        text="他撤销的 4 条都说性格不合，建议转个软客户给他试",
        source_surface="spa",
    ))
    db.commit()

    yield thread_id, [user_msg_id, asst_msg_id], manager_id, subject_id

    # Cleanup
    db.execute(
        "DELETE FROM interactions WHERE source LIKE %s",
        (f"decision_mode:{thread_id}:%",),
    )
    db.commit()


def test_decision_messages_not_in_record_timeline(db_session, setup_manager_thread):
    """SELECT WHERE subjects @> customer record — decision messages MUST
    NOT match because their subjects column is hard-coded to []."""
    _, msg_ids, _, subject_id = setup_manager_thread
    rows = db_session.fetchall(
        """SELECT id FROM interactions
            WHERE subjects @> %s::jsonb""",
        (json.dumps([{"module": "customers", "id": subject_id}]),),
    ) or []
    matched_ids = {r["id"] for r in rows}
    assert not (set(msg_ids) & matched_ids), (
        "decision messages leaked into customers timeline"
    )


def test_decision_messages_not_in_team_activity_feed(
    db_session, setup_manager_thread,
):
    """team_activity-style feed (kinds: crm_write/task/note/message)
    excludes kind='copilot_message' → decision messages don't appear."""
    _, msg_ids, _, _ = setup_manager_thread
    rows = db_session.fetchall(
        """SELECT id FROM interactions
            WHERE kind IN ('crm_write', 'task', 'note', 'message')
              AND occurred_at >= now() - interval '1 hour'""",
    ) or []
    matched = {r["id"] for r in rows}
    for mid in msg_ids:
        assert mid not in matched, f"decision msg {mid} leaked into feed"


def test_decision_messages_invisible_to_other_user_via_store(
    db_session, setup_manager_thread,
):
    """fetch_thread_messages(thread_id, manager_id=other) returns []."""
    from hermes_cloud.app.decisions.store import fetch_thread_messages
    thread_id, _, manager_id, _ = setup_manager_thread
    other_user = manager_id + 1000
    rows = fetch_thread_messages(
        db_session, thread_id=thread_id, manager_id=other_user,
    )
    assert rows == []


def test_decision_messages_invisible_to_subject(
    db_session, setup_manager_thread,
):
    """Subject (the user being discussed) cannot see manager's thread."""
    from hermes_cloud.app.decisions.store import fetch_thread_messages
    thread_id, _, _, subject_id = setup_manager_thread
    rows = fetch_thread_messages(
        db_session, thread_id=thread_id, manager_id=subject_id,
    )
    assert rows == []


def test_visibility_filter_self_only_enforced():
    """Pure unit check on the filter helper — no DB needed but kept
    here for completeness of the e2e bundle."""
    from hermes_cloud.app.scope.interaction_visibility import (
        respect_interaction_visibility,
    )
    rows = [{
        "actors": [{"type": "user", "id": 1}],
        "provenance": {"visibility": "self_only"},
        "content": {"text": "secret"},
    }]
    assert respect_interaction_visibility(rows, caller_id=2) == []
    assert respect_interaction_visibility(rows, caller_id=1) == rows


def test_admin_forensic_can_see(db_session, setup_manager_thread):
    """Admin compliance audit bypasses self_only via admin_forensic."""
    from hermes_cloud.app.scope.interaction_visibility import (
        respect_interaction_visibility,
    )
    thread_id, _, manager_id, _ = setup_manager_thread
    raw_rows = db_session.fetchall(
        """SELECT id, actors, provenance, content FROM interactions
            WHERE source LIKE %s""",
        (f"decision_mode:{thread_id}:%",),
    ) or []
    out = respect_interaction_visibility(
        raw_rows, caller_id=manager_id + 1, admin_forensic=True,
    )
    assert len(out) == 2  # user + assistant


def test_manager_self_can_see_own_thread(db_session, setup_manager_thread):
    """Manager themselves see their own thread (chatbox / SPA共享流)."""
    from hermes_cloud.app.decisions.store import fetch_thread_messages
    thread_id, _, manager_id, _ = setup_manager_thread
    rows = fetch_thread_messages(
        db_session, thread_id=thread_id, manager_id=manager_id,
    )
    assert len(rows) == 2


def test_generic_interactions_api_filters_by_visibility(
    db_session, setup_manager_thread,
):
    """``GET /api/interactions?kind=copilot_message`` from a different
    user's caller_id MUST NOT return another user's decision messages.

    Pre-spec-§5.3-fix the generic interactions API returned every row
    regardless of ``provenance.visibility``; this exercises the filter
    integration in ``hermes_cloud/app/interactions/api.py``.
    """
    from hermes_cloud.app.scope.interaction_visibility import (
        respect_interaction_visibility,
    )
    thread_id, msg_ids, manager_id, _ = setup_manager_thread
    raw_rows = db_session.fetchall(
        """SELECT id, kind, actors, provenance, content
             FROM interactions
            WHERE kind = 'copilot_message'
              AND source LIKE %s""",
        (f"decision_mode:{thread_id}:%",),
    ) or []
    # Sanity: setup did insert both user + assistant rows.
    assert len(raw_rows) == 2

    other_caller = manager_id + 7
    filtered = respect_interaction_visibility(raw_rows, caller_id=other_caller)
    # DB returns UUID objects; msg_ids are str — normalize for comparison.
    msg_id_strs = {str(m) for m in msg_ids}
    leaked = {str(r["id"]) for r in filtered} & msg_id_strs
    assert not leaked, "decision messages leaked through /api/interactions"

    # Manager themselves still gets the rows.
    self_view = respect_interaction_visibility(raw_rows, caller_id=manager_id)
    assert {str(r["id"]) for r in self_view} >= msg_id_strs
