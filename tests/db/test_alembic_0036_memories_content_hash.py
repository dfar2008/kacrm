"""0036 — schema for memory dedupe + per-specialist attribution.

Pins:
  * memories.content_hash column
  * memories.source_agent column
  * idx_memories_dedupe partial index on (scope, source_agent, content_hash)

Mirrors the resolution pattern in
``tests/db/test_alembic_0035_specialist_runtime.py``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

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


def test_memories_has_content_hash_column(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"] for c in insp.get_columns("memories")}
    assert "content_hash" in cols


def test_memories_has_source_agent_column(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"] for c in insp.get_columns("memories")}
    assert "source_agent" in cols


def test_memories_dedupe_index_exists(sync_engine):
    insp = inspect(sync_engine)
    idx_names = {ix["name"] for ix in insp.get_indexes("memories")}
    assert "idx_memories_dedupe" in idx_names


def test_memories_dedupe_index_columns(sync_engine):
    insp = inspect(sync_engine)
    for ix in insp.get_indexes("memories"):
        if ix["name"] == "idx_memories_dedupe":
            assert list(ix["column_names"]) == ["scope", "source_agent", "content_hash"]
            return
    pytest.fail("idx_memories_dedupe not found")


def test_memories_dedupe_index_is_partial(sync_engine):
    """Partial index condition should mention content_hash IS NOT NULL."""
    with sync_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT pg_get_indexdef(indexrelid) AS idx_def "
            "FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = 'idx_memories_dedupe'"
        )).fetchone()
    assert row is not None
    assert "content_hash IS NOT NULL" in row.idx_def
