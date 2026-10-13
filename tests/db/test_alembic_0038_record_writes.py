"""0038 — record_writes table + gateway_actions.record_write_token.

Pins:
  * record_writes table with the spec'd columns
  * indexes ``idx_record_writes_lookup`` (partial) + ``idx_record_writes_agent``
  * gateway_actions has the new ``record_write_token`` BIGINT column

See docs/2026-04-25-deferred-upstream-features.md §A2.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect

from _db_url import resolve_database_url


@pytest.fixture(scope="module")
def sync_engine():
    url = resolve_database_url()
    if not url:
        pytest.skip("no CRM database URL configured")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"CRM database not reachable: {exc}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_record_writes_table_exists(sync_engine):
    insp = inspect(sync_engine)
    assert "record_writes" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("record_writes")}
    assert {
        "id",
        "module",
        "record_id",
        "agent_name",
        "profile_id",
        "began_at",
        "committed_at",
        "rolled_back_at",
        "fields",
    }.issubset(cols)


def test_record_writes_indexes(sync_engine):
    insp = inspect(sync_engine)
    idx_names = {i["name"] for i in insp.get_indexes("record_writes")}
    assert "idx_record_writes_lookup" in idx_names
    assert "idx_record_writes_agent" in idx_names


def test_gateway_actions_has_record_write_token(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"]: c for c in insp.get_columns("gateway_actions")}
    assert "record_write_token" in cols
    # bigint is the canonical type — SQLAlchemy reflects PG BIGINT as BIGINT.
    assert "BIGINT" in str(cols["record_write_token"]["type"]).upper()
