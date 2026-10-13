"""provision_module / rename_module 单元测试(MagicMock,符合项目约定)。"""
from __future__ import annotations
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException


# --------------------------------------------------------------------------
# _validate_module_name
# --------------------------------------------------------------------------

class _DupDb:
    """fetchone 对查重返回 n=0(不存在)或 n=1(存在)。"""
    def __init__(self, exists: bool):
        self._exists = exists
    def fetchone(self, sql, params=None):
        return {"n": 1 if self._exists else 0}


@pytest.mark.parametrize("bad", ["", "1abc", "Abc", "a-b", "a b", "drop;",
                                  "select", "users", "pg_x"])
def test_validate_module_name_rejects_bad(bad):
    from hermes_cloud.app.crm.module_provisioning import _validate_module_name
    with pytest.raises(HTTPException):
        _validate_module_name(_DupDb(exists=False), bad)


def test_validate_module_name_rejects_duplicate():
    from hermes_cloud.app.crm.module_provisioning import _validate_module_name
    with pytest.raises(HTTPException) as ei:
        _validate_module_name(_DupDb(exists=True), "expenses")
    assert ei.value.status_code == 409


def test_validate_module_name_ok():
    from hermes_cloud.app.crm.module_provisioning import _validate_module_name
    _validate_module_name(_DupDb(exists=False), "expenses")  # 不抛即通过


# --------------------------------------------------------------------------
# _create_record_table
# --------------------------------------------------------------------------

def test_create_record_table_emits_system_columns():
    from hermes_cloud.app.crm.module_provisioning import _create_record_table
    executed = []
    db = MagicMock()
    db.execute.side_effect = lambda sql, params=None: executed.append(sql)
    _create_record_table(db, "expenses")
    blob = "\n".join(executed)
    assert 'CREATE TABLE "expenses"' in blob
    assert '"expenses_id"   BIGSERIAL PRIMARY KEY' in blob
    assert '"expenses_name" VARCHAR(255) NOT NULL' in blob
    for col in ("owner_by", "created_by", "updated_by", "deleted",
                "created_at", "updated_at"):
        assert col in blob
    assert "trg_expenses_updated_at" in blob
    assert "idx_expenses_owner" in blob
    assert "set_updated_at()" in blob


# --------------------------------------------------------------------------
# provision_module 编排
# --------------------------------------------------------------------------

def _txn_db():
    db = MagicMock()
    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn
    db.fetchall.return_value = []          # roles / permissions 空
    # fetchone: 两次查重(n=0)+ 一次取 collections id
    db.fetchone.side_effect = [
        {"n": 0}, {"n": 0}, {"id": 123},
    ]
    return db


def test_provision_module_orchestration():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = _txn_db()
    with patch.object(mp, "ModuleFieldsDb") as MF, \
         patch.object(mp, "provision_field") as pf, \
         patch.object(mp, "SchemaChangelogDb") as CL:
        res = mp.provision_module(
            db,
            {"name": "expenses", "title": "费用", "title_en": "Expenses",
             "initial_fields": [{"field_name": "amount", "field_label": "金额",
                                 "field_type": "currency"}]},
            {"id": 1, "username": "admin"}, reason="t")

    assert res == {"module_id": 123, "name": "expenses"}
    # 建表 SQL 发生
    create_sqls = [c.args[0] for c in db.execute.call_args_list]
    assert any('CREATE TABLE "expenses"' in s for s in create_sqls)
    assert any("INSERT INTO collections" in s for s in create_sqls)
    # 系统字段被灌 + 初始字段走 provision_field + changelog add_module
    assert MF.return_value.insert_field.call_count == 3   # name/owner/created_at
    pf.assert_called_once()
    assert CL.return_value.insert_entry.call_args.kwargs["op"] == "add_module"


def test_provision_module_rejects_bad_name():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.return_value = {"n": 0}
    with pytest.raises(HTTPException):
        mp.provision_module(db, {"name": "Bad Name", "title": "x"},
                            {"id": 1, "username": "a"}, reason=None)


# --------------------------------------------------------------------------
# rename_module
# --------------------------------------------------------------------------

def test_rename_module_updates_title():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn
    db.fetchone.return_value = {"id": 5, "title": "旧", "title_en": "Old"}
    with patch.object(mp, "SchemaChangelogDb") as CL:
        res = mp.rename_module(db, "renmod", "新", "New",
                               {"id": 1, "username": "a"}, reason=None)
    assert res == {"name": "renmod", "title": "新", "title_en": "New"}
    upd = [c.args[0] for c in db.execute.call_args_list]
    assert any("UPDATE collections SET title" in s for s in upd)
    assert CL.return_value.insert_entry.call_args.kwargs["op"] == "rename_module"


def test_rename_module_404_unknown():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.return_value = None
    with pytest.raises(HTTPException) as ei:
        mp.rename_module(db, "nope", "x", None, {"id": 1, "username": "a"},
                         reason=None)
    assert ei.value.status_code == 404


def test_validate_module_name_rejects_too_long():
    from hermes_cloud.app.crm.module_provisioning import _validate_module_name
    with pytest.raises(HTTPException) as ei:
        _validate_module_name(_DupDb(exists=False), "a" * 41)
    assert ei.value.status_code == 400


# --------------------------------------------------------------------------
# provision_subdetail
# --------------------------------------------------------------------------

def test_provision_subdetail_orchestration():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn
    # fetchone: validate dup(n=0), validate table(n=0), parent lookup, new id
    db.fetchone.side_effect = [
        {"n": 0}, {"n": 0}, {"id": 7}, {"id": 88},
    ]
    with patch.object(mp, "provision_field") as pf, \
         patch.object(mp, "SchemaChangelogDb") as CL:
        res = mp.provision_subdetail(
            db, "orders",
            {"name": "order_lines", "title": "明细", "detail_kind": "generic",
             "initial_fields": [{"field_name": "qty", "field_label": "数量",
                                 "field_type": "number"}]},
            {"id": 1, "username": "admin"}, reason="t")
    assert res["name"] == "order_lines"
    assert res["parent_module_id"] == 7
    assert res["detail_kind"] == "generic"
    sqls = [c.args[0] for c in db.execute.call_args_list]
    assert any('CREATE TABLE "order_lines"' in s for s in sqls)
    assert any('"orders_id" BIGINT NOT NULL' in s for s in sqls)
    pf.assert_called_once()
    assert CL.return_value.insert_entry.call_args.kwargs["op"] == "add_subdetail"


def test_provision_subdetail_rejects_bad_kind():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    with pytest.raises(HTTPException):
        mp.provision_subdetail(db, "orders",
            {"name": "x", "title": "X", "detail_kind": "weird"},
            {"id": 1, "username": "a"}, reason=None)


def test_provision_subdetail_404_unknown_parent():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.side_effect = [{"n": 0}, {"n": 0}, None]  # parent 不存在
    with pytest.raises(HTTPException) as ei:
        mp.provision_subdetail(db, "nope",
            {"name": "x", "title": "X", "detail_kind": "generic"},
            {"id": 1, "username": "a"}, reason=None)
    assert ei.value.status_code == 404


# --------------------------------------------------------------------------
# deprovision_module —— 安全删除(依赖检查 + 事务 + 归档)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("core", ["accounts", "contacts", "leads", "products"])
def test_deprovision_rejects_protected_core(core):
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.return_value = {"id": 1, "title": core, "title_en": None,
                                "parent_module_id": None}
    with pytest.raises(HTTPException) as ei:
        mp.deprovision_module(db, core, {"id": 1, "username": "a"}, reason=None)
    assert ei.value.status_code == 403
    db.execute.assert_not_called()  # 零副作用


def test_deprovision_404_unknown():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.return_value = None
    with pytest.raises(HTTPException) as ei:
        mp.deprovision_module(db, "ghost", {"id": 1, "username": "a"}, reason=None)
    assert ei.value.status_code == 404


def test_deprovision_rejects_subdetail():
    """子明细不能单删(随父走)。"""
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.return_value = {"id": 9, "title": "明细", "title_en": None,
                                "parent_module_id": 7}
    with pytest.raises(HTTPException) as ei:
        mp.deprovision_module(db, "order_lines", {"id": 1, "username": "a"},
                              reason=None)
    assert ei.value.status_code == 400
    db.execute.assert_not_called()


def test_deprovision_rejects_inbound_reference():
    """别的模块有 relation 指向它 → 拒删,避免悬空引用。"""
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.return_value = {"id": 5, "title": "供应商", "title_en": None,
                                "parent_module_id": None}
    db.fetchall.return_value = []  # 无子明细
    with patch.object(mp, "_find_inbound_relations",
                      return_value=["products.supplier_id"]):
        with pytest.raises(HTTPException) as ei:
            mp.deprovision_module(db, "suppliers", {"id": 1, "username": "a"},
                                  reason=None)
    assert ei.value.status_code == 409
    db.execute.assert_not_called()


def test_deprovision_orchestration_with_archive():
    """happy path:归档 → 清库存 → 先删子后删父 collections → DROP 表(子先父后)。"""
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn
    db.fetchone.return_value = {"id": 50, "title": "入库单", "title_en": None,
                                "parent_module_id": None}
    db.fetchall.return_value = [{"id": 51, "name": "ruku_products",
                                 "detail_kind": "product"}]
    with patch.object(mp, "_find_inbound_relations", return_value=[]), \
         patch.object(mp, "_archive_module", return_value=99) as arc, \
         patch.object(mp, "_purge_stock_residue") as purge:
        res = mp.deprovision_module(db, "ruku", {"id": 1, "username": "admin"},
                                    reason="cleanup")
    assert res["name"] == "ruku"
    assert res["archive_id"] == 99
    assert res["subdetails"] == ["ruku_products"]
    arc.assert_called_once()
    purge.assert_called_once()
    sqls = [c.args[0] for c in db.execute.call_args_list]
    # collections 删除:子(51)在父(50)之前
    del_idx = [i for i, s in enumerate(sqls) if "DELETE FROM collections" in s]
    assert len(del_idx) == 2
    # DROP:子表在父表之前,均 CASCADE
    drop_sqls = [s for s in sqls if "DROP TABLE" in s]
    assert any('"ruku_products"' in s and "CASCADE" in s for s in drop_sqls)
    assert any('"ruku"' in s and "CASCADE" in s for s in drop_sqls)
    assert drop_sqls.index(next(s for s in drop_sqls if '"ruku_products"' in s)) \
        < drop_sqls.index(next(s for s in drop_sqls if '"ruku"' in s and "products" not in s))


def test_deprovision_no_archive_skips_snapshot():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn
    db.fetchone.return_value = {"id": 60, "title": "x", "title_en": None,
                                "parent_module_id": None}
    db.fetchall.return_value = []
    with patch.object(mp, "_find_inbound_relations", return_value=[]), \
         patch.object(mp, "_archive_module") as arc, \
         patch.object(mp, "_purge_stock_residue"):
        res = mp.deprovision_module(db, "tmpmod", {"id": 1, "username": "a"},
                                    reason=None, archive=False)
    arc.assert_not_called()
    assert res["archive_id"] is None


def test_deprovision_rejects_unsafe_name():
    """DDL 防线:非法标识符名直接拒(纵深防御,即便来自 DB)。"""
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    with pytest.raises(HTTPException):
        mp.deprovision_module(db, "evil; DROP", {"id": 1, "username": "a"},
                              reason=None)
    db.execute.assert_not_called()


# --------------------------------------------------------------------------
# restore_module —— 从归档还原
# --------------------------------------------------------------------------

def test_restore_404_unknown_or_already_restored():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    db.fetchone.return_value = None  # 不存在 / 已还原
    with pytest.raises(HTTPException) as ei:
        mp.restore_module(db, 12345, {"id": 1, "username": "a"}, reason=None)
    assert ei.value.status_code == 404


def test_restore_orchestration():
    from hermes_cloud.app.crm import module_provisioning as mp
    db = MagicMock()
    @contextmanager
    def _txn():
        yield
    db.transaction.side_effect = _txn
    db.fetchone.return_value = {
        "id": 99, "module_name": "ruku", "title": "入库单", "title_en": None,
        "payload": {"collection": {"name": "ruku"}, "fields": [], "rows": [],
                    "subdetails": []},
    }
    with patch.object(mp, "_validate_module_name") as val, \
         patch.object(mp, "_rebuild_module") as rebuild:
        res = mp.restore_module(db, 99, {"id": 1, "username": "admin"},
                                reason="oops")
    assert res["name"] == "ruku"
    val.assert_called_once()      # 还原前确认名字没被占用
    rebuild.assert_called_once()
    upd = [c.args[0] for c in db.execute.call_args_list]
    assert any("UPDATE module_archive SET restored_at" in s for s in upd)
