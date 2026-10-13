"""Tests for the formula branch of ``_backfill_new_field`` and for
``recompute_formula_field`` — batched formula column population.

Driven by a fake DB that scripts the existing-rows read.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from hermes_cloud.app.crm.field_provisioning import (
    _backfill_new_field,
    recompute_formula_field,
)


class FakeDb:
    """Returns the scripted rows for the bulk SELECT; records execute()."""

    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def fetchall(self, sql, params=None):
        return [dict(r) for r in self._rows]

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def _formula_field(expr="quantity * price"):
    return {
        "field_name": "line_total",
        "field_type": "formula",
        "field_config": {"formula": {"expression": expr}},
    }


def test_backfill_formula_computes_each_row():
    rows = [
        {"salesorders_id": 1, "quantity": 2, "price": 5},
        {"salesorders_id": 2, "quantity": 3, "price": 10},
    ]
    db = FakeDb(rows)
    module = {"name": "salesorders", "id": 3}
    _backfill_new_field(db, module, _formula_field())
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert len(updates) == 2
    written = [p[0] for (_s, p) in updates]
    assert written == [Decimal(10), Decimal(30)]


def test_backfill_formula_targets_row_pk():
    rows = [{"salesorders_id": 5, "quantity": 1, "price": 1}]
    db = FakeDb(rows)
    _backfill_new_field(db, {"name": "salesorders", "id": 3}, _formula_field())
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert updates[0][1][1] == 5


def test_backfill_formula_no_rows_is_noop():
    db = FakeDb([])
    _backfill_new_field(db, {"name": "salesorders", "id": 3}, _formula_field())
    assert [s for (s, _p) in db.executed if "UPDATE" in s.upper()] == []


def test_backfill_formula_missing_ref_is_zero():
    rows = [{"salesorders_id": 1, "quantity": 4}]  # no price
    db = FakeDb(rows)
    _backfill_new_field(db, {"name": "salesorders", "id": 3}, _formula_field())
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert updates[0][1][0] == Decimal(0)


def test_backfill_formula_config_as_json_string():
    rows = [{"salesorders_id": 1, "quantity": 2, "price": 3}]
    db = FakeDb(rows)
    field = _formula_field()
    field["field_config"] = json.dumps(field["field_config"])
    _backfill_new_field(db, {"name": "salesorders", "id": 3}, field)
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert updates[0][1][0] == Decimal(6)


def test_recompute_formula_field_batches_all_rows():
    rows = [
        {"salesorders_id": 1, "quantity": 2, "price": 5},
        {"salesorders_id": 2, "quantity": 3, "price": 10},
        {"salesorders_id": 3, "quantity": 0, "price": 99},
    ]
    db = FakeDb(rows)
    recompute_formula_field(
        db, {"name": "salesorders", "id": 3}, _formula_field()
    )
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert len(updates) == 3
    assert [p[0] for (_s, p) in updates] == [
        Decimal(10), Decimal(30), Decimal(0),
    ]


def test_recompute_formula_field_new_expression():
    rows = [{"salesorders_id": 1, "quantity": 4, "price": 5}]
    db = FakeDb(rows)
    field = _formula_field(expr="quantity + price")
    recompute_formula_field(db, {"name": "salesorders", "id": 3}, field)
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert updates[0][1][0] == Decimal(9)


def test_recompute_formula_field_broken_expr_skips_row_no_raise():
    rows = [{"salesorders_id": 1, "a": 1}]
    db = FakeDb(rows)
    field = {
        "field_name": "bad",
        "field_type": "formula",
        "field_config": {"formula": {"expression": "a + ("}},
    }
    # broken expression must not raise — row simply skipped
    recompute_formula_field(db, {"name": "salesorders", "id": 3}, field)
    assert [s for (s, _p) in db.executed if "UPDATE" in s.upper()] == []
