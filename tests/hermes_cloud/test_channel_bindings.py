"""channel_bindings_db 单元测试。

测试层级：SQL 构造 + FakeDb 行为验证（无真实 PostgreSQL）。
原因：worktree 环境无隔离测试库，且 conftest.py 在没有 HERMES_TESTS_REAL_PG=1
时会 stub psycopg_pool，DbWrapper 不可实际连接。因此采用 FakeDb 记录
传入的 SQL/参数并返回预设数据，验证以下契约：
  - get() 发出正确的 WHERE 子句，命中时返回行，否则 None
  - bind() 发出 ON CONFLICT ... DO UPDATE RETURNING 并返回行
  - list_for_record() 发出正确过滤 + 排序
  - unbind() 发出 DELETE WHERE id = %s，无返回值

若日后要做集成测试，加 HERMES_TESTS_REAL_PG=1 并使用 requires_real_pg marker。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest

import hermes_cloud.app.crm.channel_bindings_db as cbd


# ---------------------------------------------------------------------------
# FakeDb helpers
# ---------------------------------------------------------------------------

class FakeDb:
    """最小 DbWrapper 替身，记录最近一次 SQL 调用。"""

    def __init__(
        self,
        fetchone_result: Optional[dict] = None,
        fetchall_result: Optional[list] = None,
        execute_result: int = 0,
    ):
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []
        self._execute_result = execute_result

        self.last_fetchone_sql: Optional[str] = None
        self.last_fetchone_args: Optional[tuple] = None
        self.last_fetchall_sql: Optional[str] = None
        self.last_fetchall_args: Optional[tuple] = None
        self.last_execute_sql: Optional[str] = None
        self.last_execute_args: Optional[tuple] = None

    def fetchone(self, sql: str, args: tuple = ()) -> Optional[dict]:
        self.last_fetchone_sql = sql
        self.last_fetchone_args = args
        return self._fetchone_result

    def fetchall(self, sql: str, args: tuple = ()) -> list[dict]:
        self.last_fetchall_sql = sql
        self.last_fetchall_args = args
        return self._fetchall_result

    def execute(self, sql: str, args: tuple = ()) -> int:
        self.last_execute_sql = sql
        self.last_execute_args = args
        return self._execute_result


_SAMPLE_ROW = {
    "id": 1,
    "channel": "feishu",
    "external_id": "ou_abc123",
    "module": "contacts",
    "record_id": 42,
    "bound_by": 7,
    "created_at": datetime(2026, 6, 23, 10, 0, 0, tzinfo=timezone.utc),
}


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

def test_get_found():
    db = FakeDb(fetchone_result=_SAMPLE_ROW)
    result = cbd.get(db, "feishu", "ou_abc123")

    assert result == _SAMPLE_ROW
    # SQL 要包含 WHERE channel = %s AND external_id = %s
    assert "channel_bindings" in db.last_fetchone_sql
    assert "WHERE" in db.last_fetchone_sql
    assert db.last_fetchone_args == ("feishu", "ou_abc123")


def test_get_not_found():
    db = FakeDb(fetchone_result=None)
    result = cbd.get(db, "feishu", "nonexistent")

    assert result is None
    assert db.last_fetchone_args == ("feishu", "nonexistent")


# ---------------------------------------------------------------------------
# bind()
# ---------------------------------------------------------------------------

def test_bind_returns_row():
    db = FakeDb(fetchone_result=_SAMPLE_ROW)
    result = cbd.bind(db, channel="feishu", external_id="ou_abc123",
                      module="contacts", record_id=42, bound_by=7)

    assert result == _SAMPLE_ROW
    sql = db.last_fetchone_sql
    assert "INSERT INTO channel_bindings" in sql
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql
    assert "RETURNING" in sql


def test_bind_args_order():
    db = FakeDb(fetchone_result=_SAMPLE_ROW)
    cbd.bind(db, channel="wecom", external_id="wm_xyz",
             module="leads", record_id=99, bound_by=None)

    assert db.last_fetchone_args == ("wecom", "wm_xyz", "leads", 99, None)


def test_bind_upsert_updates_existing():
    """重复绑定同一 external_id 到不同记录 —— SQL 应使用 DO UPDATE。"""
    updated_row = {**_SAMPLE_ROW, "record_id": 100}
    db = FakeDb(fetchone_result=updated_row)
    result = cbd.bind(db, channel="feishu", external_id="ou_abc123",
                      module="contacts", record_id=100, bound_by=7)

    assert result["record_id"] == 100
    assert "DO UPDATE" in db.last_fetchone_sql


# ---------------------------------------------------------------------------
# list_for_record()
# ---------------------------------------------------------------------------

def test_list_for_record_empty():
    db = FakeDb(fetchall_result=[])
    result = cbd.list_for_record(db, "contacts", 42)

    assert result == []
    assert db.last_fetchall_args == ("contacts", 42)


def test_list_for_record_returns_rows():
    rows = [_SAMPLE_ROW, {**_SAMPLE_ROW, "id": 2, "channel": "wecom", "external_id": "wm_xyz"}]
    db = FakeDb(fetchall_result=rows)
    result = cbd.list_for_record(db, "contacts", 42)

    assert len(result) == 2
    sql = db.last_fetchall_sql
    assert "channel_bindings" in sql
    assert "ORDER BY" in sql
    assert db.last_fetchall_args == ("contacts", 42)


# ---------------------------------------------------------------------------
# unbind()
# ---------------------------------------------------------------------------

def test_unbind_sends_delete():
    db = FakeDb(execute_result=1)
    result = cbd.unbind(db, 1)

    assert result is None  # 无返回值
    assert "DELETE" in db.last_execute_sql
    assert "channel_bindings" in db.last_execute_sql
    assert db.last_execute_args == (1,)


def test_unbind_passes_correct_id():
    db = FakeDb(execute_result=1)
    cbd.unbind(db, 999)

    assert db.last_execute_args == (999,)
