"""_build_subdetails / _expand_product 契约(TDD)。"""
from __future__ import annotations

from typing import Optional

from hermes_cloud.app.crm.ai_attributes import _build_subdetails, _expand_product


class FakeDb:
    """按调用顺序弹出 fetchall / fetchone 预设结果。"""

    def __init__(self):
        self.fetchall_calls = []
        self.fetchone_calls = []
        self._fetchall_queue = []
        self._fetchone_queue = []

    def set_fetchall_queue(self, *results):
        self._fetchall_queue = list(results)

    def set_fetchone_queue(self, *results):
        self._fetchone_queue = list(results)

    def fetchall(self, sql, args=()):
        self.fetchall_calls.append((sql, args))
        return self._fetchall_queue.pop(0) if self._fetchall_queue else []

    def fetchone(self, sql, args=()) -> Optional[dict]:
        self.fetchone_calls.append((sql, args))
        return self._fetchone_queue.pop(0) if self._fetchone_queue else None


def test_generic_subdetail_rows_stripped_of_bookkeeping():
    db = FakeDb()
    db.set_fetchall_queue(
        [{"id": 9, "name": "orders_lines", "title": "订单行", "detail_kind": "generic"}],
        [{"orders_lines_id": 1, "orders_id": 7, "qty": 2, "memo": "x",
          "created_by": 1, "deleted": False}],
    )
    out = _build_subdetails(db, "orders", 7)
    assert len(out) == 1
    assert out[0]["module"] == "orders_lines"
    assert out[0]["title"] == "订单行"
    row = out[0]["rows"][0]
    assert row == {"qty": 2, "memo": "x"}          # PK/父FK/簿记列剔除
    assert "product" not in row                      # 非产品明细不展开


def test_product_subdetail_expands_full_product_fields():
    db = FakeDb()
    db.set_fetchall_queue(
        [{"id": 5, "name": "salesorders_products", "title": "产品明细", "detail_kind": "product"}],
        [{"salesorders_products_id": 1, "salesorders_id": 7, "products_id": 42,
          "quantity": 3, "price": 100, "created_by": 1, "deleted": False}],
    )
    db.set_fetchone_queue(
        {"products_id": 42, "name": "泵A", "sku": "P-42", "spec": "DN50",
         "created_by": 1, "deleted": False},
    )
    out = _build_subdetails(db, "salesorders", 7)
    row = out[0]["rows"][0]
    assert row["quantity"] == 3 and row["price"] == 100
    assert row["product"] == {"name": "泵A", "sku": "P-42", "spec": "DN50"}
    assert "products_id" not in row


def test_no_children_returns_empty():
    db = FakeDb()
    db.set_fetchall_queue([])                        # 无子模块
    assert _build_subdetails(db, "accounts", 1) == []


def test_child_query_error_is_best_effort():
    class BoomDb(FakeDb):
        def fetchall(self, sql, args=()):
            raise RuntimeError("db down")
    assert _build_subdetails(BoomDb(), "orders", 7) == []


def test_db_or_record_none_returns_empty():
    assert _build_subdetails(None, "orders", 7) == []
    assert _build_subdetails(FakeDb(), "orders", None) == []


def test_row_limit_applied_in_sql():
    db = FakeDb()
    db.set_fetchall_queue(
        [{"id": 9, "name": "orders_lines", "title": "行", "detail_kind": "generic"}],
        [{"orders_lines_id": 1, "orders_id": 7, "qty": 1}],
    )
    _build_subdetails(db, "orders", 7)
    rows_sql, rows_args = db.fetchall_calls[1]
    assert "LIMIT" in rows_sql
    assert rows_args[-1] == 50                       # _SUBDETAIL_ROW_LIMIT


def test_unsafe_child_name_skipped():
    db = FakeDb()
    db.set_fetchall_queue(
        [{"id": 9, "name": "bad name; DROP", "title": "x", "detail_kind": "generic"}],
    )
    assert _build_subdetails(db, "orders", 7) == []  # 非法标识符跳过,不拼 SQL
    assert len(db.fetchall_calls) == 1   # 仅 children 查询,未触发 rows 拼 SQL


def test_row_query_error_skips_that_child():
    class RowsBoomDb(FakeDb):
        def fetchall(self, sql, args=()):
            self.fetchall_calls.append((sql, args))
            if "collections" in sql:
                return [{"id": 9, "name": "orders_lines", "title": "行",
                         "detail_kind": "generic"}]
            raise RuntimeError("rows query down")
    db = RowsBoomDb()
    assert _build_subdetails(db, "orders", 7) == []   # 该子模块被跳过,其余无


def test_unsafe_module_name_returns_empty():
    db = FakeDb()
    assert _build_subdetails(db, "bad module; DROP", 7) == []
    assert db.fetchall_calls == []   # 校验发生在任何查库之前


def test_expand_product_none_id_returns_none():
    assert _expand_product(FakeDb(), None) is None
    assert _expand_product(FakeDb(), 0) is None


def test_expand_product_missing_returns_none():
    db = FakeDb()                                    # fetchone 队列空 → None
    assert _expand_product(db, 42) is None
