"""0035 — schema for v2.3 specialist runtime.

Pins the four schema deltas the runtime depends on:
  * gateway_actions: executing_specialist / delegation_parent_draft_id /
    delegation_root_draft_id / reject_reason_type
  * specialist_delegation_acl table
  * specialist_delegation_edges table
  * memories.content_redacted shadow column

The project has no ``hermes_cloud.app.db.engine`` module — alembic's env.py
resolves the DB URL from ``HERMES_CRM_DATABASE_URL`` / ``CRM_DATABASE_URL``
(falling back to ``sqlalchemy.url`` in alembic.ini). We mirror that
resolution via ``tests/_db_url.py`` so the test inspects the exact same
database alembic migrated.
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
        # Probe the connection so we skip cleanly if PG is down.
        with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"CRM database not reachable: {exc}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_gateway_actions_has_specialist_columns(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"] for c in insp.get_columns("gateway_actions")}
    assert {
        "executing_specialist",
        "delegation_parent_draft_id",
        "delegation_root_draft_id",
        "reject_reason_type",
    }.issubset(cols)


def test_specialist_delegation_acl_table_exists(sync_engine):
    insp = inspect(sync_engine)
    assert "specialist_delegation_acl" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("specialist_delegation_acl")}
    assert {"parent_specialist", "child_specialist", "allowed_action_kinds"}.issubset(cols)


def test_specialist_delegation_edges_table_exists(sync_engine):
    insp = inspect(sync_engine)
    assert "specialist_delegation_edges" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("specialist_delegation_edges")}
    assert {
        "id",
        "parent_draft_id",
        "child_draft_id",
        "parent_specialist",
        "child_specialist",
        "edge_outcome",
        "created_at",
    }.issubset(cols)


def test_memories_has_content_redacted_column(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"] for c in insp.get_columns("memories")}
    assert "content_redacted" in cols
