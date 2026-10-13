"""Save.insert/update 同事务原子 + 记忆复利钩子 (P1)。"""
from __future__ import annotations
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest


def _make_saver():
    from hermes.crm.save import Save
    s = Save(MagicMock())
    s.module = {"id": 1, "name": "accounts"}
    s.module_id = 1
    s.table_name = "accounts"
    s.user_id = 3
    s.is_admin = True
    return s


def _common_patches(s):
    """Patch 掉与本测试无关的校验/投影/记忆身份分支。"""
    mi = MagicMock()
    mi.is_linked_table.return_value = False  # 跳过 memory_entity_id 注入
    return [
        patch.object(s, "_check_permission"),
        patch.object(s, "_validate_required"),
        patch.object(s, "_validate_select_options"),
        patch.object(s, "_project_data_to_columns", side_effect=lambda d: dict(d)),
        patch.object(s, "_derive_department", return_value=None),
        patch.object(s, "_recompute_formula_fields"),
        patch.object(s, "_execute_field_writeback"),
        patch("hermes.crm.save.memory_identity", mi),
    ]


def test_insert_wraps_in_transaction_and_calls_compounding():
    s = _make_saver()
    db = s._ensure_db()
    ctx = _common_patches(s)
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6], ctx[7], \
         patch.object(s, "_assign_autonumber_fields"), \
         patch.object(s, "_emit_business_write") as emit, \
         patch("hermes.crm.save.memory_compounding") as mc:
        db.fetchone.return_value = {"accounts_id": 99}
        rid = s.insert({"accounts_name": "Acme"})

    assert rid == 99
    db.transaction.assert_called()
    mc.sync_entity_attributes.assert_called_once()
    mc.schedule_field_embedding.assert_called_once()
    emit.assert_called_once()


def test_update_wraps_in_transaction_and_calls_compounding():
    s = _make_saver()
    s.record_id = 7
    db = s._ensure_db()
    ctx = _common_patches(s)
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6], ctx[7], \
         patch.object(s, "_emit_business_write") as emit, \
         patch("hermes.crm.save.memory_compounding") as mc:
        db.fetchone.return_value = {"accounts_id": 7, "deleted": False,
                                    "owner_by": 3, "created_by": 3}
        rid = s.update({"industry": "saas"})

    assert rid == 7
    db.transaction.assert_called()
    mc.sync_entity_attributes.assert_called_once()
    mc.schedule_field_embedding.assert_called_once()
    emit.assert_called_once()


def test_emit_failure_rolls_back_write():
    """interaction 发射失败 → 整笔 insert 抛出（同事务回滚，不吞）。"""
    s = _make_saver()
    db = s._ensure_db()

    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn  # 真实事务语义,不抑制异常

    ctx = _common_patches(s)
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6], ctx[7], \
         patch.object(s, "_assign_autonumber_fields"), \
         patch.object(s, "_emit_business_write",
                      side_effect=RuntimeError("event store down")), \
         patch("hermes.crm.save.memory_compounding"):
        db.fetchone.return_value = {"accounts_id": 99}
        with pytest.raises(RuntimeError, match="event store down"):
            s.insert({"accounts_name": "Acme"})
