"""记忆复利:业务字段同事务写入 memory_entities.attributes + after_commit embedding。"""
from __future__ import annotations
import json
from unittest.mock import MagicMock


def test_sync_entity_attributes_writes_jsonb():
    from hermes.crm.memory_compounding import sync_entity_attributes
    db = MagicMock()
    db.fetchone.return_value = {"memory_entity_id": 42}

    sync_entity_attributes(
        db, "accounts", 7,
        payload={"accounts_name": "Acme", "industry": "saas",
                 "owner_by": 3, "created_by": 3},  # bookkeeping 应被过滤
    )

    sql = db.execute.call_args[0][0]
    assert "memory_entities" in sql
    assert "attributes" in sql
    params = db.execute.call_args[0][1]
    blob = json.loads(params[0]) if isinstance(params[0], str) else params[0]
    assert blob.get("industry") == "saas"
    assert "owner_by" not in blob


def test_sync_entity_attributes_noop_when_unlinked():
    from hermes.crm.memory_compounding import sync_entity_attributes
    db = MagicMock()
    db.fetchone.return_value = {"memory_entity_id": None}
    sync_entity_attributes(db, "accounts", 7, payload={"industry": "saas"})
    db.execute.assert_not_called()


def test_schedule_field_embedding_registers_after_commit():
    from hermes.crm.memory_compounding import schedule_field_embedding
    db = MagicMock()
    db.fetchone.return_value = {"memory_entity_id": 42}

    schedule_field_embedding(
        db, "accounts", 7, user_id=3,
        payload={"accounts_name": "Acme", "industry": "saas", "owner_by": 3},
        headline="Acme",
    )
    db.after_commit.assert_called_once()
    cb = db.after_commit.call_args[0][0]
    assert callable(cb)


def test_schedule_field_embedding_invokes_upsert(monkeypatch):
    import hermes.memory.vector_store as vs
    from hermes.crm.memory_compounding import schedule_field_embedding
    calls = {}
    monkeypatch.setattr(
        vs, "upsert_memory",
        lambda **kw: calls.update(kw) or MagicMock(),
    )
    db = MagicMock()
    db.fetchone.return_value = {"memory_entity_id": 42}
    captured = {}
    db.after_commit.side_effect = lambda cb: captured.update(cb=cb)

    schedule_field_embedding(
        db, "accounts", 7, user_id=3,
        payload={"industry": "saas"}, headline="Acme",
    )
    captured["cb"]()  # 模拟提交后触发
    assert calls["user_id"] == 3
    assert "saas" in calls["content"]
    assert calls["meta"]["entity_id"] == 42
