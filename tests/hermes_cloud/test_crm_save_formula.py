"""Tests for ``Save._recompute_formula_fields`` — write-time formula
recomputation in the save layer.

Isolated with a fake DB + stubbed field catalog; the row read after
the write is scripted so the test is pure.
"""
from __future__ import annotations

from decimal import Decimal


class FakeDb:
    def __init__(self, row):
        self._row = row
        self.executed = []

    def fetchone(self, sql, params=None):
        return dict(self._row)

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def _save(db, fields):
    from hermes.crm.save import Save

    s = Save(db)
    s.module = {"name": "salesorders", "id": 3}
    s.table_name = "salesorders"
    s.module_id = 3
    s.record_id = 7
    s._fields_cache = fields
    return s


def _formula_field(name, expr):
    return {
        "field_name": name,
        "field_type": "formula",
        "field_config": {"formula": {"expression": expr}},
    }


def test_recompute_writes_numeric_value():
    db = FakeDb(row={"quantity": 3, "price": 10, "line_total": None})
    s = _save(db, [_formula_field("line_total", "quantity * price")])
    s._recompute_formula_fields()
    updates = [(q, p) for (q, p) in db.executed if "UPDATE" in q.upper()]
    assert len(updates) == 1
    assert updates[0][1][0] == Decimal(30)
    assert updates[0][1][1] == 7  # record_id


def test_recompute_missing_ref_treated_as_zero():
    db = FakeDb(row={"quantity": 3, "total": None})
    s = _save(db, [_formula_field("total", "quantity * price")])
    s._recompute_formula_fields()
    updates = [(q, p) for (q, p) in db.executed if "UPDATE" in q.upper()]
    assert updates[0][1][0] == Decimal(0)


def test_recompute_multiple_formula_fields():
    db = FakeDb(row={"a": 2, "b": 5, "sum": None, "prod": None})
    s = _save(db, [_formula_field("sum", "a + b"),
                   _formula_field("prod", "a * b")])
    s._recompute_formula_fields()
    updates = [(q, p) for (q, p) in db.executed if "UPDATE" in q.upper()]
    written = sorted(p[0] for (_q, p) in updates)
    assert written == [Decimal(7), Decimal(10)]


def test_recompute_no_formula_fields_is_noop():
    db = FakeDb(row={"name": "x"})
    s = _save(db, [{"field_name": "name", "field_type": "text"}])
    s._recompute_formula_fields()
    assert db.executed == []


def test_recompute_bad_expression_skips_field_no_raise():
    # a broken formula must not abort the whole save
    db = FakeDb(row={"a": 1, "broken": None, "ok": None})
    s = _save(db, [_formula_field("broken", "a + ("),
                   _formula_field("ok", "a + 1")])
    s._recompute_formula_fields()  # must not raise
    updates = [(q, p) for (q, p) in db.executed if "UPDATE" in q.upper()]
    # only the valid field is written
    assert len(updates) == 1
    assert updates[0][1][0] == Decimal(2)
