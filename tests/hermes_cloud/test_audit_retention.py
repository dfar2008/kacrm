"""Tests for ``app.scope.retention`` — Phase 5.5 audit retention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from hermes_cloud.app.scope.retention import cleanup_audit_log


class _FakeDb:
    def __init__(self, *, old_rows=None, raise_on_delete=False, raise_on_scan=False):
        self.old_rows = old_rows or []
        self.delete_calls = []
        self.commits = 0
        self.raise_on_delete = raise_on_delete
        self.raise_on_scan = raise_on_scan

    def fetchall(self, sql, params=()):
        if self.raise_on_scan:
            raise RuntimeError("scan failed")
        if "FROM audit_log" in sql:
            return self.old_rows
        return []

    def execute(self, sql, params=()):
        if "DELETE FROM audit_log" in sql:
            if self.raise_on_delete:
                raise RuntimeError("delete failed")
            self.delete_calls.append(params)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def test_no_old_rows_returns_zero():
    out = cleanup_audit_log(_FakeDb())
    assert out == {"archived": 0, "deleted": 0}


def test_archives_and_deletes_old_rows():
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(days=120)
    rows = [
        {"id": 1, "caller_id": 7, "scope_kind": "team",
         "resource_kind": "x", "resource_filter": {},
         "occurred_at": old},
        {"id": 2, "caller_id": 7, "scope_kind": "org",
         "resource_kind": "y", "resource_filter": {},
         "occurred_at": old},
    ]
    db = _FakeDb(old_rows=rows)
    written = []
    def _writer(label, rs):
        for r in rs:
            written.append((label, r))
        return len(list(rs)) if False else len(written)  # trivial counter

    out = cleanup_audit_log(db, archive_writer=_writer)
    assert out["archived"] >= 2
    assert out["deleted"] == 2
    assert len(db.delete_calls) == 1


def test_dry_run_archives_but_doesnt_delete():
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(days=120)
    rows = [
        {"id": 1, "caller_id": 7, "scope_kind": "team",
         "resource_kind": "x", "resource_filter": {}, "occurred_at": old},
    ]
    db = _FakeDb(old_rows=rows)
    written = []
    out = cleanup_audit_log(
        db,
        archive_writer=lambda label, rs: (
            written.extend(rs) or len(list(rs)) if False else 1
        ),
        dry_run=True,
    )
    assert out["archived"] >= 1
    assert out["deleted"] == 0  # dry_run protected


def test_archive_failure_doesnt_delete():
    """If archive write fails for any month, deletes are NOT
    performed (data safety — never lose what wasn't archived)."""
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(days=120)
    rows = [
        {"id": 1, "caller_id": 7, "scope_kind": "team",
         "resource_kind": "x", "resource_filter": {},
         "occurred_at": old},
    ]
    db = _FakeDb(old_rows=rows)
    def _bad_writer(label, rs):
        raise IOError("S3 down")
    out = cleanup_audit_log(db, archive_writer=_bad_writer)
    assert out["error"].startswith("archive_write_failed")
    assert out["deleted"] == 0
    assert db.delete_calls == []


def test_scan_failure_returns_error_no_writes():
    db = _FakeDb(raise_on_scan=True)
    out = cleanup_audit_log(db)
    assert out["error"] == "scan_failed"
    assert out["archived"] == 0
    assert out["deleted"] == 0


def test_delete_failure_rolls_back():
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(days=120)
    rows = [{"id": 1, "caller_id": 7, "scope_kind": "team",
             "resource_kind": "x", "resource_filter": {}, "occurred_at": old}]
    db = _FakeDb(old_rows=rows, raise_on_delete=True)
    out = cleanup_audit_log(
        db,
        archive_writer=lambda label, rs: 1,
    )
    assert out["error"] == "delete_failed"
    assert out["archived"] == 1  # archive succeeded
    assert out["deleted"] == 0
