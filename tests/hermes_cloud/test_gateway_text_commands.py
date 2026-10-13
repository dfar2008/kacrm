"""Text-command parser for gateway approvals (Charter §1 渠道闭环).

Covers:

* :func:`parse_command` recognizes 通过/同意/approve/✅ + 拒绝/否决/reject
  with both half- and full-width spaces, optional leading slash, and
  English aliases. Non-commands return ``None``.
* :func:`try_approval_command` resolves a UUID prefix against
  ``gateway_actions`` filtered by ``via_channel`` + ``profile_id``,
  then calls ``dispatcher.approve``/``reject`` and returns a Chinese
  reply string. Wrong-channel rows are filtered out at lookup so the
  cross-channel guard never trips here.
* Ambiguous prefixes (≥2 matches) refuse to act and ask for a longer
  prefix — a defensive measure since the prefix is only 8 hex chars.
"""
from __future__ import annotations

from typing import Any, Dict, List


# --- parse_command --------------------------------------------------------


def test_parse_command_chinese_approve():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("通过 abcd1234") == ("approve", "abcd1234")
    assert parse_command("同意 abcd1234") == ("approve", "abcd1234")
    assert parse_command("/approve abcd1234") == ("approve", "abcd1234")
    assert parse_command("✅ abcd1234") == ("approve", "abcd1234")


def test_parse_command_chinese_reject():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("拒绝 abcd1234") == ("reject", "abcd1234")
    assert parse_command("否决 abcd1234") == ("reject", "abcd1234")
    assert parse_command("reject abcd1234") == ("reject", "abcd1234")


def test_parse_command_tolerates_fullwidth_space():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("通过　abcd1234") == ("approve", "abcd1234")


def test_parse_command_rejects_non_commands():
    from hermes.action_gateway.text_commands import parse_command
    assert parse_command("hi there") is None
    assert parse_command("") is None
    assert parse_command("通过") is None  # missing prefix
    assert parse_command("通过 ") is None
    assert parse_command("通过 zzz") is None  # not hex
    assert parse_command(None) is None  # type: ignore[arg-type]


# --- try_approval_command -------------------------------------------------


class _FakeDb:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.approved: List[str] = []
        self.rejected: List[str] = []

    def fetchall(self, sql: str, params: tuple = ()):
        # Match the LIKE prefix + via_channel + profile_id filter.
        prefix_pat, channel, profile_id = params
        prefix = prefix_pat.rstrip("%")
        return [
            {"id": r["id"]}
            for r in self._rows
            if r["id"].startswith(prefix)
            and r["origin"]["channel"] == channel
            and r["profile_id"] == profile_id
            and r["state"] == "pending_approval"
        ]


def _patch_dispatcher(monkeypatch, *, raises=None):
    from hermes.action_gateway import dispatcher

    approved: List[tuple] = []
    rejected: List[tuple] = []

    def _approve(action_id, approver_id, *, via_channel, db):
        if raises:
            raise raises
        approved.append((action_id, approver_id, via_channel))
        return {"id": action_id, "state": "executed"}

    def _reject(action_id, approver_id, *, via_channel, db):
        if raises:
            raise raises
        rejected.append((action_id, approver_id, via_channel))
        return {"id": action_id, "state": "rejected"}

    monkeypatch.setattr(dispatcher, "approve", _approve)
    monkeypatch.setattr(dispatcher, "reject", _reject)
    return approved, rejected


def test_try_approval_command_approve_happy(monkeypatch):
    from hermes.action_gateway.text_commands import try_approval_command

    db = _FakeDb([{
        "id": "abcd1234-aaaa-bbbb-cccc-000000000001",
        "origin": {"channel": "wecom_ilink"},
        "profile_id": "42",
        "state": "pending_approval",
    }])
    approved, _ = _patch_dispatcher(monkeypatch)

    reply = try_approval_command(
        "通过 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None
    assert "已通过" in reply
    assert len(approved) == 1
    assert approved[0][0] == "abcd1234-aaaa-bbbb-cccc-000000000001"
    assert approved[0][2] == "wecom_ilink"


def test_try_approval_command_reject_happy(monkeypatch):
    from hermes.action_gateway.text_commands import try_approval_command

    db = _FakeDb([{
        "id": "deadbeef-aaaa-bbbb-cccc-000000000002",
        "origin": {"channel": "wecom_ilink"},
        "profile_id": "42",
        "state": "pending_approval",
    }])
    _, rejected = _patch_dispatcher(monkeypatch)

    reply = try_approval_command(
        "拒绝 deadbeef", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None
    assert "已拒绝" in reply
    assert len(rejected) == 1


def test_try_approval_command_returns_none_for_chat(monkeypatch):
    from hermes.action_gateway.text_commands import try_approval_command
    db = _FakeDb([])
    reply = try_approval_command(
        "正常聊天内容", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is None


def test_try_approval_command_no_match(monkeypatch):
    from hermes.action_gateway.text_commands import try_approval_command
    _patch_dispatcher(monkeypatch)
    db = _FakeDb([])
    reply = try_approval_command(
        "通过 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None and "未找到" in reply


def test_try_approval_command_ambiguous_prefix(monkeypatch):
    from hermes.action_gateway.text_commands import try_approval_command
    _patch_dispatcher(monkeypatch)
    db = _FakeDb([
        {"id": "abcd1234-1111-1111-1111-111111111111",
         "origin": {"channel": "wecom_ilink"}, "profile_id": "42",
         "state": "pending_approval"},
        {"id": "abcd1234-2222-2222-2222-222222222222",
         "origin": {"channel": "wecom_ilink"}, "profile_id": "42",
         "state": "pending_approval"},
    ])
    reply = try_approval_command(
        "通过 abcd1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None and "更长的 ID" in reply


def test_try_approval_command_filters_by_channel(monkeypatch):
    """A wecom_ilink reply MUST NOT match a feishu-origin action — the
    lookup filters before the dispatcher's cross-channel guard would
    fire, so the user gets a clear "未找到" rather than a confusing
    "请到原始审批渠道处理"."""
    from hermes.action_gateway.text_commands import try_approval_command
    _patch_dispatcher(monkeypatch)
    db = _FakeDb([{
        "id": "feed1234-aaaa-bbbb-cccc-000000000003",
        "origin": {"channel": "feishu"},  # different channel
        "profile_id": "42",
        "state": "pending_approval",
    }])
    reply = try_approval_command(
        "通过 feed1234", user_id=42, via_channel="wecom_ilink", db=db,
    )
    assert reply is not None and "未找到" in reply
