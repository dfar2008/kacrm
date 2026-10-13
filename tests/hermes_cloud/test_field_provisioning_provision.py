"""Tests for provision_field — the shared field-creation helper.

No live DB: a MagicMock DbWrapper captures every db.execute /
db.insert call. db.transaction() is patched to a no-op context
manager so the `with db.transaction():` block runs inline.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from hermes_cloud.app.crm.field_provisioning import provision_field


def _make_db() -> MagicMock:
    db = MagicMock()

    @contextmanager
    def _txn():
        yield

    db.transaction.side_effect = _txn
    db.id.return_value = 77            # field_id returned by insert_field
    return db


_MODULE = {"id": 3, "name": "accounts", "table_name": "accounts"}
_ACTOR = {"id": 9, "username": "admin"}


def _executed_sql(db: MagicMock) -> list[str]:
    """All SQL strings passed to db.execute()."""
    return [c[0][0] for c in db.execute.call_args_list]


def test_provision_field_returns_field_id_and_name():
    db = _make_db()
    out = provision_field(
        db, _MODULE,
        {"field_name": "region", "field_label": "Region",
         "field_type": "text"},
        _ACTOR, reason=None,
    )
    assert out == {"field_id": 77, "field_name": "region"}


def test_provision_field_emits_alter_table_add_column():
    db = _make_db()
    provision_field(
        db, _MODULE,
        {"field_name": "region", "field_label": "Region",
         "field_type": "text"},
        _ACTOR, reason=None,
    )
    sql = " ".join(_executed_sql(db))
    assert 'ALTER TABLE "accounts"' in sql
    assert 'ADD COLUMN IF NOT EXISTS "region" TEXT' in sql


def test_provision_field_number_type_uses_numeric_column():
    db = _make_db()
    provision_field(
        db, _MODULE,
        {"field_name": "score", "field_label": "Score",
         "field_type": "number"},
        _ACTOR, reason=None,
    )
    assert 'ADD COLUMN IF NOT EXISTS "score" NUMERIC' in " ".join(
        _executed_sql(db))


def test_provision_field_autonumber_creates_sequence():
    db = _make_db()
    provision_field(
        db, _MODULE,
        {"field_name": "order_no", "field_label": "Order No",
         "field_type": "autoNumber"},
        _ACTOR, reason=None,
    )
    sql = " ".join(_executed_sql(db))
    assert 'ADD COLUMN IF NOT EXISTS "order_no" VARCHAR(64)' in sql
    assert "CREATE SEQUENCE IF NOT EXISTS autonum_accounts_order_no" in sql


def test_provision_field_non_autonumber_creates_no_sequence():
    db = _make_db()
    provision_field(
        db, _MODULE,
        {"field_name": "region", "field_label": "Region",
         "field_type": "text"},
        _ACTOR, reason=None,
    )
    assert "CREATE SEQUENCE" not in " ".join(_executed_sql(db))


def test_provision_field_writes_changelog_op_add_field():
    db = _make_db()
    provision_field(
        db, _MODULE,
        {"field_name": "region", "field_label": "Region",
         "field_type": "text"},
        _ACTOR, reason="quarterly cleanup",
    )
    # db.insert(table, payload) — find the schema_changelog insert and
    # confirm it records the add_field operation.
    insert_calls = {c[0][0]: c[0][1] for c in db.insert.call_args_list}
    assert "schema_changelog" in insert_calls
    assert insert_calls["schema_changelog"]["op"] == "add_field"


def test_provision_field_unknown_type_raises_400():
    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        provision_field(
            db, _MODULE,
            {"field_name": "x", "field_label": "X",
             "field_type": "hologram"},
            _ACTOR, reason=None,
        )
    assert exc.value.status_code == 400


def test_provision_field_bad_identifier_raises_400():
    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        provision_field(
            db, _MODULE,
            {"field_name": "2bad", "field_label": "Bad",
             "field_type": "text"},
            _ACTOR, reason=None,
        )
    assert exc.value.status_code == 400


def test_provision_field_runs_inside_transaction():
    db = _make_db()
    provision_field(
        db, _MODULE,
        {"field_name": "region", "field_label": "Region",
         "field_type": "text"},
        _ACTOR, reason=None,
    )
    db.transaction.assert_called_once()
