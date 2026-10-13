"""``gateway_actions.origin`` + cross-channel approval guard.

Covers Charter §1 (渠道闭环) plumbing in the dispatcher:

* ``submit(origin=...)`` round-trips into the ``origin`` JSONB column
  (and falls back to ``{"channel": "webui"}`` when the caller passes
  ``None`` so legacy callers still work).
* ``approve(via_channel=...)`` and ``reject(via_channel=...)`` raise
  :class:`ActionWrongChannelError` when ``via_channel`` does not match
  the action's recorded ``origin.channel``.
* Rows whose ``origin`` is NULL (legacy backfill) are treated as
  ``webui``-originated for backwards-compat — a Feishu approval attempt
  on such a row still gets rejected, only WebUI can clear it.

These tests exercise the dispatcher functions directly with a fake
``DbWrapper`` so they don't need a live Postgres.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class _FakeDb:
    """Minimal DbWrapper stand-in: records execute() calls + serves
    fetchone() / fetchall() from an in-memory ``gateway_actions`` table.
    """

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []
        self.executes: List[tuple] = []

    # --- execute / fetch surface used by dispatcher ---
    def execute(self, sql: str, params: tuple = ()):
        self.executes.append((sql, params))
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO gateway_actions"):
            # INSERT params (in order): id, profile_id, agent_name, kind,
            # target, params, draft_id, origin, executing_specialist,
            # delegation_parent_draft_id, delegation_root_draft_id,
            # record_write_token (Charter §A2 lock), source_skill (v2).
            (
                action_id, profile_id, agent_name, kind,
                target_json, params_json, draft_id, origin_json,
                executing_specialist, delegation_parent_draft_id,
                delegation_root_draft_id, record_write_token,
                source_skill,
            ) = params
            self.rows.append({
                "id": action_id,
                "profile_id": profile_id,
                "agent_name": agent_name,
                "kind": kind,
                "target": json.loads(target_json) if target_json else None,
                "params": json.loads(params_json) if params_json else None,
                "state": "pending_approval",
                "draft_id": draft_id,
                "origin": json.loads(origin_json) if origin_json else None,
                "executing_specialist": executing_specialist,
                "delegation_parent_draft_id": delegation_parent_draft_id,
                "delegation_root_draft_id": delegation_root_draft_id,
                "record_write_token": record_write_token,
                "source_skill": source_skill,
                "reject_reason_type": None,
                "trust_level": None,
                "approver": None,
            })
        elif sql_norm.startswith("UPDATE gateway_actions SET trust_level"):
            level, action_id = params
            for r in self.rows:
                if r["id"] == action_id:
                    r["trust_level"] = level
        elif sql_norm.startswith("UPDATE gateway_actions SET state = 'approved'"):
            approver, action_id = params
            for r in self.rows:
                if r["id"] == action_id:
                    r["state"] = "approved"
                    r["approver"] = approver
        elif sql_norm.startswith("UPDATE gateway_actions SET state = 'rejected'"):
            approver, action_id = params
            for r in self.rows:
                if r["id"] == action_id:
                    r["state"] = "rejected"
                    r["approver"] = approver

    def fetchone(self, sql: str, params: tuple = ()):
        if "SELECT * FROM gateway_actions WHERE id" in sql:
            (aid,) = params
            for r in self.rows:
                if r["id"] == aid:
                    return r
        return None

    def fetchall(self, sql: str, params: tuple = ()):
        return []

    def fetchcolumn(self, sql, params=()):
        return None


def _stub_trust_engine(monkeypatch, *, level: int = 2, decision="REQUIRE_APPROVAL"):
    """Pin trust verdict so submit() lands in pending state."""
    from hermes.action_gateway import dispatcher
    from hermes.trust.engine import Decision

    fake = type(
        "V", (),
        {"level": level, "decision": getattr(Decision, decision),
         "reason": ""},
    )()
    monkeypatch.setattr(dispatcher, "trust_decide", lambda **kw: fake)


def test_submit_persists_origin_jsonb(monkeypatch):
    from hermes.action_gateway import dispatcher

    _stub_trust_engine(monkeypatch)
    db = _FakeDb()

    origin = {
        "channel": "feishu",
        "external_user_id": "ou_xxx",
        "external_message_id": "om_xxx",
    }
    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        target={"module": "accounts"}, params={"op": "create"},
        origin=origin, db=db,
    )

    assert row["origin"] == origin
    assert row["state"] == "pending_approval"


def test_submit_defaults_origin_to_webui(monkeypatch):
    from hermes.action_gateway import dispatcher

    _stub_trust_engine(monkeypatch)
    db = _FakeDb()

    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        target=None, params=None, db=db,  # origin omitted
    )

    assert row["origin"] == {"channel": "webui"}


def test_submit_coerces_invalid_origin_to_webui(monkeypatch, caplog):
    from hermes.action_gateway import dispatcher

    _stub_trust_engine(monkeypatch)
    db = _FakeDb()

    # No channel key → invalid; dispatcher must coerce, not crash.
    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        origin={"external_user_id": "ou_x"},  # missing channel
        db=db,
    )
    assert row["origin"] == {"channel": "webui"}


def test_approve_rejects_wrong_channel(monkeypatch):
    from hermes.action_gateway import dispatcher

    _stub_trust_engine(monkeypatch)
    db = _FakeDb()

    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        origin={"channel": "feishu", "external_user_id": "ou_x"},
        db=db,
    )

    # WebUI tries to approve a Feishu-originated action — refuse.
    import pytest
    with pytest.raises(dispatcher.ActionWrongChannelError) as exc_info:
        dispatcher.approve(row["id"], 1, via_channel="webui", db=db)
    msg = str(exc_info.value)
    assert "feishu" in msg and "webui" in msg


def test_approve_accepts_matching_channel(monkeypatch):
    from hermes.action_gateway import dispatcher

    _stub_trust_engine(monkeypatch)
    db = _FakeDb()

    # Stub _execute_action so we don't need a real executor.
    monkeypatch.setattr(dispatcher, "_execute_action", lambda *a, **kw: None)
    monkeypatch.setattr(
        dispatcher, "_record_draft_adoption", lambda *a, **kw: None,
    )

    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        origin={"channel": "feishu", "external_user_id": "ou_x"},
        db=db,
    )

    out = dispatcher.approve(row["id"], 1, via_channel="feishu", db=db)
    assert out["state"] == "approved"


def test_reject_rejects_wrong_channel(monkeypatch):
    from hermes.action_gateway import dispatcher

    _stub_trust_engine(monkeypatch)
    db = _FakeDb()
    monkeypatch.setattr(
        dispatcher, "_record_draft_adoption", lambda *a, **kw: None,
    )

    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        origin={"channel": "wecom_ilink", "external_user_id": "wxid_y"},
        db=db,
    )

    import pytest
    with pytest.raises(dispatcher.ActionWrongChannelError):
        dispatcher.reject(row["id"], 1, via_channel="discord", db=db)


def test_legacy_null_origin_treated_as_webui(monkeypatch):
    """Rows pre-dating the 0028 migration have origin=NULL.
    The guard treats them as webui — a Feishu adapter must not be able
    to silently approve historical pending rows whose true origin we
    no longer know."""
    from hermes.action_gateway import dispatcher

    db = _FakeDb()
    # Hand-craft a legacy row.
    db.rows.append({
        "id": "legacy-1",
        "profile_id": "p1",
        "agent_name": "intake",
        "kind": "crm.write",
        "target": None,
        "params": None,
        "state": "pending_approval",
        "draft_id": None,
        "origin": None,
        "trust_level": 2,
        "approver": None,
    })

    monkeypatch.setattr(dispatcher, "_execute_action", lambda *a, **kw: None)
    monkeypatch.setattr(
        dispatcher, "_record_draft_adoption", lambda *a, **kw: None,
    )

    import pytest
    with pytest.raises(dispatcher.ActionWrongChannelError):
        dispatcher.approve("legacy-1", 1, via_channel="feishu", db=db)

    # WebUI clears it fine.
    out = dispatcher.approve("legacy-1", 1, via_channel="webui", db=db)
    assert out["state"] == "approved"
