"""Phase 4.3 — text-command revert / force-revert / edit-stub.

Covers:

* :func:`parse_command` recognizes 撤销 / 撤回 / revert / undo (with
  case-insensitive English aliases and fullwidth space tolerance) AND
  the stronger 强制撤销 / 强撤 / force-revert. Order matters: the
  stronger form is matched first so 强制撤销 doesn't accidentally
  resolve to ("revert", "强制") or similar.
* :func:`try_gateway_command` (alias of :func:`try_approval_command`):
    - executed action + 撤销 -> dispatcher.revert(force=False), reply
      ``已撤销 <short> ✅``.
    - executed action + 强制撤销 -> dispatcher.revert(force=True),
      reply ``已强制撤销 <short> ✅``.
    - revert returns state='conflict' -> reply names the force-revert
      escape hatch.
    - already reverted (state lookup) -> "已经撤销过了".
    - missing prefix -> "找不到 …".
    - ambiguous prefix -> "找到多条匹配 …".
    - 改 stub -> redirect message ("暂不支持直接修改 …").
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# --- parse_command --------------------------------------------------------


def test_parse_command_revert_chinese():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("撤销 abcd1234") == ("revert", "abcd1234")
    assert parse_command("撤回 abcd1234") == ("revert", "abcd1234")
    assert parse_command("revert ABCD1234") == ("revert", "abcd1234")
    assert parse_command("undo abcd1234") == ("revert", "abcd1234")


def test_parse_command_force_revert_matched_before_revert():
    """强制撤销 contains 撤销 as a substring; the parser MUST match the
    stronger form first."""
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("强制撤销 abcd1234") == ("force_revert", "abcd1234")
    assert parse_command("强撤 abcd1234") == ("force_revert", "abcd1234")
    assert parse_command("force-revert abcd1234") == (
        "force_revert", "abcd1234",
    )
    assert parse_command("force_revert abcd1234") == (
        "force_revert", "abcd1234",
    )


def test_parse_command_revert_fullwidth_space():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("撤销　abcd1234") == ("revert", "abcd1234")
    assert parse_command("强制撤销　abcd1234") == ("force_revert", "abcd1234")


def test_parse_command_edit_stub():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("改 abcd1234 phone=13800000000") == (
        "edit", "abcd1234",
    )
    assert parse_command("修改 abcd1234 name=张三") == ("edit", "abcd1234")


def test_parse_command_revert_rejects_non_commands():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("撤销") is None  # missing prefix
    assert parse_command("撤销 zzz") is None  # not hex
    assert parse_command("强制") is None
    assert parse_command("hi 撤销 abcd1234") is None  # leading garbage


# --- try_gateway_command (revert) -----------------------------------------


class _FakeRevertDb:
    """Minimal DbWrapper double for the revert lookup query."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def fetchall(self, sql: str, params: tuple = ()):
        prefix_pat, channel, profile_id = params
        prefix = prefix_pat.rstrip("%")
        # Mirror the lookup filter in _resolve_revert_target.
        out = [
            {"id": r["id"], "state": r["state"]}
            for r in self._rows
            if r["id"].startswith(prefix)
            and r["origin"]["channel"] == channel
            and r["profile_id"] == profile_id
            and r["state"] in ("executed", "reverted", "conflict")
        ]
        return out[:2]


def _patch_revert(monkeypatch, *, returns_state: str = "reverted",
                  raises: Optional[Exception] = None):
    """Patch ``dispatcher.revert`` and return a calls list."""
    from hermes.action_gateway import dispatcher

    calls: List[Dict[str, Any]] = []

    def _revert(action_id, user_id, *, force=False, db=None):
        calls.append({
            "action_id": action_id,
            "user_id": user_id,
            "force": force,
        })
        if raises:
            raise raises
        return {"id": action_id, "state": returns_state}

    monkeypatch.setattr(dispatcher, "revert", _revert)
    return calls


def test_try_gateway_command_revert_happy(monkeypatch):
    from hermes.action_gateway.text_commands import try_gateway_command

    db = _FakeRevertDb([{
        "id": "abcd1234-aaaa-bbbb-cccc-000000000001",
        "origin": {"channel": "wecom_ilink"},
        "profile_id": "42",
        "state": "executed",
    }])
    calls = _patch_revert(monkeypatch, returns_state="reverted")

    reply = try_gateway_command(
        "撤销 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None
    assert "已撤销 abcd1234" in reply
    assert "✅" in reply
    assert len(calls) == 1
    assert calls[0]["force"] is False
    assert calls[0]["action_id"] == "abcd1234-aaaa-bbbb-cccc-000000000001"


def test_try_gateway_command_force_revert_happy(monkeypatch):
    from hermes.action_gateway.text_commands import try_gateway_command

    db = _FakeRevertDb([{
        "id": "abcd1234-aaaa-bbbb-cccc-000000000001",
        "origin": {"channel": "wecom_ilink"},
        "profile_id": "42",
        # Phase 4.2: a previous 撤销 attempt left it in conflict state.
        "state": "conflict",
    }])
    calls = _patch_revert(monkeypatch, returns_state="reverted")

    reply = try_gateway_command(
        "强制撤销 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None
    assert "已强制撤销 abcd1234" in reply
    assert calls[0]["force"] is True


def test_try_gateway_command_revert_conflict(monkeypatch):
    from hermes.action_gateway.text_commands import try_gateway_command

    db = _FakeRevertDb([{
        "id": "abcd1234-aaaa-bbbb-cccc-000000000001",
        "origin": {"channel": "wecom_ilink"},
        "profile_id": "42",
        "state": "executed",
    }])
    _patch_revert(monkeypatch, returns_state="conflict")

    reply = try_gateway_command(
        "撤销 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None
    assert "❌" in reply
    assert "强制撤销 abcd1234" in reply  # surface the escape hatch


def test_try_gateway_command_revert_already_reverted(monkeypatch):
    from hermes.action_gateway.text_commands import try_gateway_command

    db = _FakeRevertDb([{
        "id": "abcd1234-aaaa-bbbb-cccc-000000000001",
        "origin": {"channel": "wecom_ilink"},
        "profile_id": "42",
        "state": "reverted",
    }])
    calls = _patch_revert(monkeypatch)

    reply = try_gateway_command(
        "撤销 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None
    assert "已经撤销过了" in reply
    # Should short-circuit before calling dispatcher.
    assert len(calls) == 0


def test_try_gateway_command_revert_not_found(monkeypatch):
    from hermes.action_gateway.text_commands import try_gateway_command
    _patch_revert(monkeypatch)
    db = _FakeRevertDb([])

    reply = try_gateway_command(
        "撤销 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None and "找不到" in reply


def test_try_gateway_command_revert_ambiguous(monkeypatch):
    from hermes.action_gateway.text_commands import try_gateway_command
    _patch_revert(monkeypatch)
    db = _FakeRevertDb([
        {"id": "abcd1234-1111-1111-1111-111111111111",
         "origin": {"channel": "wecom_ilink"}, "profile_id": "42",
         "state": "executed"},
        {"id": "abcd1234-2222-2222-2222-222222222222",
         "origin": {"channel": "wecom_ilink"}, "profile_id": "42",
         "state": "executed"},
    ])
    reply = try_gateway_command(
        "撤销 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None and "找到多条匹配" in reply


def test_try_gateway_command_revert_filters_by_channel(monkeypatch):
    """A wecom_ilink reply must NOT match a feishu-origin action."""
    from hermes.action_gateway.text_commands import try_gateway_command
    _patch_revert(monkeypatch)
    db = _FakeRevertDb([{
        "id": "feed1234-aaaa-bbbb-cccc-000000000003",
        "origin": {"channel": "feishu"},
        "profile_id": "42",
        "state": "executed",
    }])
    reply = try_gateway_command(
        "撤销 feed1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None and "找不到" in reply


def test_try_gateway_command_edit_stub_redirects(monkeypatch):
    from hermes.action_gateway.text_commands import try_gateway_command
    db = _FakeRevertDb([])  # parser doesn't even hit the DB for edit
    reply = try_gateway_command(
        "改 abcd1234 phone=13800000000",
        user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None
    assert "暂不支持" in reply
    assert "撤销 abcd1234" in reply  # tells operator how to proceed
