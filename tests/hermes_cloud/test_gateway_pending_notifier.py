"""Pending-approval notifier registry + dispatcher integration.

Covers Charter §1 渠道闭环 delivery plumbing:

* :func:`register` puts one notifier per channel; re-registering
  replaces (single source of truth per channel).
* :func:`notify_pending` swallows errors so a flaky channel adapter
  cannot break the dispatcher.
* :func:`notify_pending` logs + returns cleanly when no notifier is
  registered for the originating channel (legacy / misconfigured tenant).
* The WebUI no-op is registered at import time so callers never see a
  "no notifier for webui" warning on the default path.
* ``dispatcher.submit`` invokes ``notify_pending`` after the action
  lands in pending_approval state — wrapped in ``db.after_commit``
  when the wrapper supports it, eager otherwise.
* Notification fires **only** when the action is genuinely pending
  (auto-executed actions don't ping the channel — there's nothing to
  approve).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List


# --- shared fake DB -------------------------------------------------------


class _FakeDb:
    def __init__(self, *, supports_after_commit: bool = True):
        self.rows: List[Dict[str, Any]] = []
        self._after_commit_cbs: List[Callable[[], None]] = []
        self._supports = supports_after_commit

    def execute(self, sql: str, params: tuple = ()):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO gateway_actions"):
            (
                action_id, profile_id, agent_name, kind,
                target_json, params_json, draft_id, origin_json,
                executing_specialist,
                delegation_parent_draft_id,
                delegation_root_draft_id,
                record_write_token,
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
                "trust_level": None,
                "approver": None,
            })
        elif sql_norm.startswith("UPDATE gateway_actions SET trust_level"):
            level, action_id = params
            for r in self.rows:
                if r["id"] == action_id:
                    r["trust_level"] = level
        elif sql_norm.startswith("UPDATE gateway_actions SET state = 'executed'"):
            (action_id,) = params
            for r in self.rows:
                if r["id"] == action_id:
                    r["state"] = "executed"

    def fetchone(self, sql: str, params: tuple = ()):
        if "SELECT * FROM gateway_actions WHERE id" in sql:
            (aid,) = params
            for r in self.rows:
                if r["id"] == aid:
                    return r
        return None

    def fetchall(self, sql, params=()): return []

    def fetchcolumn(self, sql, params=()): return None

    def after_commit(self, cb):
        if not self._supports:
            raise AttributeError("after_commit not supported")
        self._after_commit_cbs.append(cb)

    def commit(self):
        for cb in self._after_commit_cbs:
            cb()
        self._after_commit_cbs.clear()


def _stub_trust(monkeypatch, *, decision="REQUIRE_APPROVAL", level=2):
    from hermes.action_gateway import dispatcher
    from hermes.trust.engine import Decision
    v = type("V", (), {"level": level,
                       "decision": getattr(Decision, decision),
                       "reason": ""})()
    monkeypatch.setattr(dispatcher, "trust_decide", lambda **kw: v)


# --- registry tests -------------------------------------------------------


def test_register_replaces_previous_notifier():
    from hermes.action_gateway import notifiers

    calls = []
    notifiers.register("test_channel_x", lambda a, o: calls.append("v1"))
    notifiers.register("test_channel_x", lambda a, o: calls.append("v2"))
    notifiers.notify_pending({"id": "x"}, {"channel": "test_channel_x"})
    assert calls == ["v2"]


def test_register_rejects_invalid():
    import pytest
    from hermes.action_gateway import notifiers

    with pytest.raises(ValueError):
        notifiers.register("", lambda a, o: None)
    with pytest.raises(TypeError):
        notifiers.register("ch", "not-callable")  # type: ignore[arg-type]


def test_webui_no_op_registered_at_import():
    from hermes.action_gateway import notifiers
    assert notifiers.get("webui") is not None
    # Calling it must be silent & raise nothing.
    notifiers.notify_pending(
        {"id": "any"}, {"channel": "webui"},
    )


def test_notify_pending_swallows_notifier_exceptions(caplog):
    from hermes.action_gateway import notifiers

    def _boom(action, origin):
        raise RuntimeError("channel SDK exploded")

    notifiers.register("test_boom_ch", _boom)
    # Must not raise.
    notifiers.notify_pending(
        {"id": "a1"}, {"channel": "test_boom_ch"},
    )


def test_notify_pending_unknown_channel_logs(caplog):
    import logging
    from hermes.action_gateway import notifiers
    caplog.set_level(logging.WARNING, logger="hermes_cloud.app.gateway.notifiers")
    notifiers.notify_pending({"id": "a1"}, {"channel": "_no_such_channel_xyz"})
    assert any(
        "notify_pending_no_notifier_for_channel" in r.getMessage()
        for r in caplog.records
    )


# --- dispatcher integration ----------------------------------------------


def test_dispatcher_calls_notifier_after_commit(monkeypatch):
    from hermes.action_gateway import dispatcher, notifiers

    _stub_trust(monkeypatch, decision="REQUIRE_APPROVAL")
    db = _FakeDb(supports_after_commit=True)

    captured: List[tuple] = []
    notifiers.register(
        "test_ch_after_commit",
        lambda action, origin: captured.append((action["id"], origin)),
    )

    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        target={"module": "accounts"}, params={"op": "create"},
        origin={"channel": "test_ch_after_commit", "external_user_id": "u1"},
        db=db,
    )

    # Hook scheduled, but not fired until commit.
    assert captured == []
    db.commit()
    assert len(captured) == 1
    assert captured[0][0] == row["id"]
    assert captured[0][1]["channel"] == "test_ch_after_commit"


def test_dispatcher_falls_back_to_eager_when_no_after_commit(monkeypatch):
    from hermes.action_gateway import dispatcher, notifiers

    _stub_trust(monkeypatch, decision="REQUIRE_APPROVAL")
    db = _FakeDb(supports_after_commit=False)
    # Strip the attribute entirely so the dispatcher's hasattr() is False.
    delattr(type(db), "after_commit")

    captured: List[Any] = []
    notifiers.register(
        "test_ch_eager",
        lambda action, origin: captured.append(action["id"]),
    )

    row = dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        origin={"channel": "test_ch_eager"},
        db=db,
    )
    assert captured == [row["id"]]


def test_dispatcher_skips_notifier_when_action_auto_executed(monkeypatch):
    """If trust says EXECUTE the action runs synchronously and lands in
    state='executed' — there is nothing to approve, so no notifier."""
    from hermes.action_gateway import dispatcher, notifiers

    _stub_trust(monkeypatch, decision="EXECUTE", level=3)
    monkeypatch.setattr(dispatcher, "_execute_action", lambda *a, **kw: None)

    # Mark row executed since we stubbed _execute_action.
    db = _FakeDb()
    captured: List[Any] = []
    notifiers.register(
        "test_ch_no_notify",
        lambda action, origin: captured.append(action["id"]),
    )

    # Patch _fetch_action to report executed state for the integration.
    real_fetch = dispatcher._fetch_action

    def _patched_fetch(aid, db_):
        row = real_fetch(aid, db_)
        if row:
            row = dict(row)
            row["state"] = "executed"
        return row

    monkeypatch.setattr(dispatcher, "_fetch_action", _patched_fetch)

    dispatcher.submit(
        profile_id="p1", agent_name="intake", kind="email.send",
        origin={"channel": "test_ch_no_notify"},
        db=db,
    )
    db.commit()
    assert captured == [], "notifier must not fire for executed actions"
