"""Pure unit tests for record_collaborators data layer."""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest


def test_list_collaborators_returns_rows_for_module_record():
    """list_collaborators 查 (module_id, record_id) 返回所有 collaborator 行(含 user 信息)."""
    from hermes_cloud.app.crm.collaborators import list_collaborators
    db = MagicMock()
    db.fetchall.return_value = [
        {"user_id": 10, "username": "alice", "realname": "Alice",
         "can_view": True, "can_edit": False, "created_at": "2026-05-26T10:00:00Z"},
        {"user_id": 11, "username": "bob", "realname": "Bob",
         "can_view": True, "can_edit": True, "created_at": "2026-05-26T11:00:00Z"},
    ]
    rows = list_collaborators(db, module_id=5, record_id=100)
    assert len(rows) == 2
    assert rows[0]["user_id"] == 10
    assert rows[1]["can_edit"] is True
    # SQL 应当 JOIN users 表
    sql = db.fetchall.call_args[0][0]
    assert "record_collaborators" in sql
    assert "JOIN" in sql.upper() and "users" in sql
    assert "module_id" in sql and "record_id" in sql


def test_upsert_collaborator_writes_row():
    """upsert_collaborator 用 ON CONFLICT DO UPDATE 写一行."""
    from hermes_cloud.app.crm.collaborators import upsert_collaborator
    db = MagicMock()
    upsert_collaborator(db, module_id=5, record_id=100, user_id=10,
                        can_view=True, can_edit=False)
    sql = db.execute.call_args[0][0]
    assert "INSERT INTO record_collaborators" in sql
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql
    # 参数顺序与 SQL 占位符对齐:(module_id, record_id, user_id, can_view, can_edit)
    assert db.execute.call_args[0][1] == (5, 100, 10, True, False)


def test_delete_collaborator_targets_unique_triple():
    """delete_collaborator 按 (module_id, record_id, user_id) 三元组删除."""
    from hermes_cloud.app.crm.collaborators import delete_collaborator
    db = MagicMock()
    db.execute.return_value = 1  # db.execute 返回 int (受影响行数)
    deleted = delete_collaborator(db, module_id=5, record_id=100, user_id=10)
    assert deleted is True
    sql = db.execute.call_args[0][0]
    assert "DELETE FROM record_collaborators" in sql
    assert "module_id" in sql and "record_id" in sql and "user_id" in sql


def test_delete_collaborator_returns_false_when_no_match():
    """删不存在的 collaborator 应返回 False。"""
    from hermes_cloud.app.crm.collaborators import delete_collaborator
    db = MagicMock()
    db.execute.return_value = 0  # 没有命中任何行
    deleted = delete_collaborator(db, module_id=5, record_id=100, user_id=99)
    assert deleted is False


def test_shared_record_ids_for_user_returns_set():
    """shared_record_ids_for_user(uid, module_id) 返回 user 在该模块下可见的 record_id 集合."""
    from hermes_cloud.app.crm.collaborators import shared_record_ids_for_user
    db = MagicMock()
    db.fetchall.return_value = [{"record_id": 100}, {"record_id": 200}, {"record_id": 300}]
    ids = shared_record_ids_for_user(db, user_id=10, module_id=5)
    assert ids == {100, 200, 300}
    sql = db.fetchall.call_args[0][0]
    assert "record_collaborators" in sql
    assert "user_id" in sql and "module_id" in sql
    assert "can_view" in sql or "can_edit" in sql


def test_can_edit_check_returns_bool():
    """user_can_edit_record 检查 (uid, module_id, record_id) 是否在 collaborator 表里且 can_edit=True."""
    from hermes_cloud.app.crm.collaborators import user_can_edit_record
    db = MagicMock()
    db.fetchone.return_value = {"can_edit": True}
    assert user_can_edit_record(db, user_id=10, module_id=5, record_id=100) is True

    db.fetchone.return_value = {"can_edit": False}
    assert user_can_edit_record(db, user_id=10, module_id=5, record_id=100) is False

    db.fetchone.return_value = None
    assert user_can_edit_record(db, user_id=10, module_id=5, record_id=100) is False
