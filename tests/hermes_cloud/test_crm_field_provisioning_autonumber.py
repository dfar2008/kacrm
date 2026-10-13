"""Tests for the autoNumber branch of ``_backfill_new_field``.

``_backfill_new_field`` is the extension-point stub created in Phase
P0. Phase P2 fleshes out its autoNumber branch: iterate existing rows
ordered by ``id``, draw ``nextval`` from the field's SEQUENCE, format,
and write the new column.

We drive it with a fake DB recorder so the test stays pure — no
Postgres. The fake records every SQL string + params and answers
``fetchall``/``fetchone`` from scripted return values.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from hermes_cloud.app.crm.field_provisioning import _backfill_new_field


class FakeDb:
    """Minimal DbWrapper stand-in.

    ``nextval`` calls are answered from a counter; ``fetchall`` returns
    the scripted existing-row id list; ``execute`` is recorded.
    """

    def __init__(self, existing_ids):
        self._existing_ids = existing_ids
        self._seq = 0
        self.executed = []  # list[(sql, params)]

    def fetchall(self, sql, params=None):
        # the "list existing rows ordered by id" query
        return [{"id": rid} for rid in self._existing_ids]

    def fetchone(self, sql, params=None):
        # nextval('autonum_...') query
        self._seq += 1
        return {"nextval": self._seq}

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def _autonumber_field():
    return {
        "field_name": "orderno",
        "field_type": "autoNumber",
        "field_config": {
            "autonumber": {
                "prefix": "SO-",
                "digits": 5,
                "start": 1,
                "date_token": "",
            }
        },
    }


def test_backfill_assigns_numbers_in_id_order():
    db = FakeDb(existing_ids=[10, 11, 12])
    module = {"name": "salesorders", "id": 3}
    _backfill_new_field(db, module, _autonumber_field())

    # one UPDATE per existing row, ascending seq values
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert len(updates) == 3
    written = [p[0] for (_s, p) in updates]
    assert written == ["SO-00001", "SO-00002", "SO-00003"]


def test_backfill_targets_correct_row_pk():
    db = FakeDb(existing_ids=[10, 11])
    module = {"name": "salesorders", "id": 3}
    _backfill_new_field(db, module, _autonumber_field())
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    # each UPDATE param tuple is (value, row_id)
    row_ids = [p[1] for (_s, p) in updates]
    assert row_ids == [10, 11]


def test_backfill_no_rows_is_noop():
    db = FakeDb(existing_ids=[])
    module = {"name": "salesorders", "id": 3}
    _backfill_new_field(db, module, _autonumber_field())
    assert [s for (s, _p) in db.executed if "UPDATE" in s.upper()] == []


def test_backfill_uses_field_sequence_name():
    db = FakeDb(existing_ids=[10])
    module = {"name": "salesorders", "id": 3}
    _backfill_new_field(db, module, _autonumber_field())
    # the nextval query must name autonum_<module>_<field>
    # FakeDb.fetchone doesn't inspect sql, so assert via a spy:
    seen = {"sql": None}
    orig = db.fetchone

    def spy(sql, params=None):
        seen["sql"] = sql
        return orig(sql, params)

    db.fetchone = spy  # type: ignore[method-assign]
    db2 = FakeDb(existing_ids=[10])
    db2.fetchone = spy  # type: ignore[method-assign]
    _backfill_new_field(db2, module, _autonumber_field())
    assert "autonum_salesorders_orderno" in (seen["sql"] or "")


def test_backfill_field_config_as_json_string():
    # field_config can arrive as a JSON string from a raw row read
    db = FakeDb(existing_ids=[10])
    module = {"name": "salesorders", "id": 3}
    field = _autonumber_field()
    field["field_config"] = json.dumps(field["field_config"])
    _backfill_new_field(db, module, field)
    updates = [(s, p) for (s, p) in db.executed if "UPDATE" in s.upper()]
    assert updates[0][1][0] == "SO-00001"
