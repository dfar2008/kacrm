"""resolve_subjects_by_identifier 单元测试。

测试重点：
  - external_id + channel 命中 channel_bindings → 返回正确 hit
  - external_id + channel 无绑定 (get=None) → 不产生 hit
  - email 命中时走 email 路径（不受 external_id 影响）
  - external_id 优先于 email/phone（解析顺序验证）

所有测试用 monkeypatch 打桩 channel_bindings_db.get，
用 FakeDb 模拟 DbWrapper（参考 test_channel_bindings.py 风格）。
"""
from __future__ import annotations

from typing import Any, Optional

import pytest


# ---------------------------------------------------------------------------
# FakeDb — 最小 DbWrapper 替身
# ---------------------------------------------------------------------------

class FakeDb:
    """记录调用、返回预设数据的 DbWrapper 替身。"""

    def __init__(
        self,
        fetchone_result: Optional[dict] = None,
    ):
        self._fetchone_result = fetchone_result
        self.fetchone_calls: list[tuple] = []

    def fetchone(self, sql: str, args: tuple = ()) -> Optional[dict]:
        self.fetchone_calls.append((sql, args))
        return self._fetchone_result

    def fetchall(self, sql: str, args: tuple = ()) -> list[dict]:
        return []

    def execute(self, sql: str, args: tuple = ()) -> int:
        return 0


# ---------------------------------------------------------------------------
# 辅助：导入被测函数
# ---------------------------------------------------------------------------

def _resolve(**kwargs):
    from hermes_cloud.app.touchpoints._im import resolve_subjects_by_identifier
    return resolve_subjects_by_identifier(**kwargs)


# ---------------------------------------------------------------------------
# external_id + channel 命中 channel_bindings
# ---------------------------------------------------------------------------

def test_external_id_channel_hit(monkeypatch):
    """channel_bindings_db.get 返回绑定行 → resolve 返回对应 hit。"""
    binding_row = {
        "id": 1,
        "channel": "feishu",
        "external_id": "ou_abc123",
        "module": "contacts",
        "record_id": 7,
        "bound_by": None,
        "created_at": None,
    }

    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: binding_row)

    db = FakeDb()
    result = _resolve(external_id="ou_abc123", channel="feishu", db=db)

    assert result == [{"module": "contacts", "id": 7}]


def test_external_id_channel_hit_leads(monkeypatch):
    """绑定到 leads 模块时也能正确映射。"""
    binding_row = {
        "id": 2,
        "channel": "wecom",
        "external_id": "wm_xyz",
        "module": "leads",
        "record_id": 99,
        "bound_by": 5,
        "created_at": None,
    }

    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: binding_row)

    db = FakeDb()
    result = _resolve(external_id="wm_xyz", channel="wecom", db=db)

    assert result == [{"module": "leads", "id": 99}]


def test_external_id_channel_hit_record_id_is_int(monkeypatch):
    """record_id 为字符串时应被 int() 转换。"""
    binding_row = {
        "id": 3,
        "channel": "dingtalk",
        "external_id": "dt_001",
        "module": "contacts",
        "record_id": "42",   # 字符串形式
        "bound_by": None,
        "created_at": None,
    }

    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: binding_row)

    db = FakeDb()
    result = _resolve(external_id="dt_001", channel="dingtalk", db=db)

    assert result == [{"module": "contacts", "id": 42}]
    assert isinstance(result[0]["id"], int)


# ---------------------------------------------------------------------------
# external_id + channel 无绑定
# ---------------------------------------------------------------------------

def test_external_id_channel_no_binding(monkeypatch):
    """get 返回 None → external_id 不产生 hit，结果为空列表。"""
    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: None)

    db = FakeDb()
    result = _resolve(external_id="unknown_id", channel="feishu", db=db)

    assert result == []


# ---------------------------------------------------------------------------
# 无 channel 时 external_id 不触发 channel_bindings 查询
# ---------------------------------------------------------------------------

def test_external_id_without_channel_not_queried(monkeypatch):
    """只有 external_id 但没有 channel 时，不应调用 channel_bindings_db.get。"""
    called = []

    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: called.append((ch, eid)) or None)

    db = FakeDb()
    result = _resolve(external_id="ou_abc", db=db)  # 无 channel

    assert result == []
    assert called == [], "不应调用 channel_bindings_db.get"


# ---------------------------------------------------------------------------
# email 命中时仍走 email（不回归）
# ---------------------------------------------------------------------------

def test_email_hit_not_affected_by_external_id(monkeypatch):
    """email 命中时，即便同时传了 external_id+channel，email 路径正常工作。

    场景：channel_bindings 无绑定（get=None），但 email 在 contacts 表有命中。
    """
    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: None)

    # FakeDb.fetchone 对 email SQL 返回 contacts 行
    contact_row = {"id": 55}
    db = FakeDb(fetchone_result=contact_row)

    result = _resolve(
        email="test@example.com",
        external_id="ou_abc",
        channel="feishu",
        db=db,
    )

    # external_id 无命中，email 走完后命中 contacts
    assert result == [{"module": "contacts", "id": 55}]


def test_email_hit_skips_phone(monkeypatch):
    """email 命中后不应继续查 phone。"""
    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: None)

    contact_row = {"id": 10}
    db = FakeDb(fetchone_result=contact_row)

    result = _resolve(
        email="a@b.com",
        phone="13800138000",
        db=db,
    )

    # email 命中后 phone 不被查 → 只有一个 hit（contacts via email）
    assert len(result) == 1
    assert result[0] == {"module": "contacts", "id": 10}


# ---------------------------------------------------------------------------
# external_id 优先于 email（解析顺序验证）
# ---------------------------------------------------------------------------

def test_external_id_takes_priority_over_email(monkeypatch):
    """external_id+channel 命中后，即便也传了 email，不再走 email 查询。"""
    binding_row = {
        "id": 1,
        "channel": "feishu",
        "external_id": "ou_priority",
        "module": "contacts",
        "record_id": 7,
        "bound_by": None,
        "created_at": None,
    }

    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: binding_row)

    # 如果 email 被查，FakeDb.fetchone 也会返回 row（id=99）
    # 但因为 external_id 已命中，email 不应被触发
    db = FakeDb(fetchone_result={"id": 99})

    result = _resolve(
        external_id="ou_priority",
        channel="feishu",
        email="test@example.com",
        db=db,
    )

    # 只有 external_id 的命中（id=7），不含 email 的命中（id=99）
    assert result == [{"module": "contacts", "id": 7}]
    # FakeDb.fetchone 未被调用（没走 email 路径）
    assert db.fetchone_calls == []


# ---------------------------------------------------------------------------
# 无任何标识符 → 空列表
# ---------------------------------------------------------------------------

def test_no_identifiers_returns_empty(monkeypatch):
    import hermes_cloud.app.crm.channel_bindings_db as cbd
    monkeypatch.setattr(cbd, "get", lambda db, ch, eid: None)

    db = FakeDb()
    result = _resolve(db=db)

    assert result == []
