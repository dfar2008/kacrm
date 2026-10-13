"""Tests for ``Save._assign_autonumber_fields`` — the create-time
autoNumber assignment hook.

``Save`` is metadata-driven and DB-backed; we isolate the new method
with a fake DB + a stubbed field catalog so the test stays pure.
"""
from __future__ import annotations


class FakeDb:
    def __init__(self):
        self._seq = 0
        self.executed = []

    def fetchone(self, sql, params=None):
        self._seq += 1
        return {"nextval": self._seq}

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def _save(db, fields):
    from hermes.crm.save import Save

    s = Save(db)
    s.module = {"name": "salesorders", "id": 3}
    s.table_name = "salesorders"
    s.module_id = 3
    s.record_id = 99
    s._fields_cache = fields
    return s


def _autonumber_field(name="orderno"):
    return {
        "field_name": name,
        "field_type": "autoNumber",
        "field_config": {
            "autonumber": {"prefix": "SO-", "digits": 5,
                           "start": 1, "date_token": ""},
        },
    }


def test_assign_autonumber_writes_formatted_value():
    db = FakeDb()
    s = _save(db, [_autonumber_field()])
    s._assign_autonumber_fields({})  # no incoming value
    updates = [(q, p) for (q, p) in db.executed if "UPDATE" in q.upper()]
    assert len(updates) == 1
    assert updates[0][1][0] == "SO-00001"
    assert updates[0][1][1] == 99  # record_id


def test_assign_autonumber_skips_when_value_supplied():
    db = FakeDb()
    s = _save(db, [_autonumber_field()])
    s._assign_autonumber_fields({"orderno": "SO-EXISTING"})
    assert [q for (q, _p) in db.executed if "UPDATE" in q.upper()] == []


def test_assign_autonumber_skips_when_empty_string_supplied_is_filled():
    # an empty string counts as "no value" → assign
    db = FakeDb()
    s = _save(db, [_autonumber_field()])
    s._assign_autonumber_fields({"orderno": "   "})
    updates = [(q, p) for (q, p) in db.executed if "UPDATE" in q.upper()]
    assert len(updates) == 1


def test_assign_autonumber_noop_when_no_autonumber_fields():
    db = FakeDb()
    s = _save(db, [{"field_name": "name", "field_type": "text"}])
    s._assign_autonumber_fields({})
    assert db.executed == []


def test_assign_autonumber_multiple_fields():
    db = FakeDb()
    s = _save(db, [_autonumber_field("orderno"),
                   _autonumber_field("ticketno")])
    s._assign_autonumber_fields({})
    updates = [(q, p) for (q, p) in db.executed if "UPDATE" in q.upper()]
    assert len(updates) == 2
