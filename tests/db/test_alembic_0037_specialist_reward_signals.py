"""0037 — specialist_reward_signals + draft-id type reconciliation.

Pins:
  * specialist_reward_signals table with (draft_id, specialist, signal,
    edge_id) columns
  * gateway_actions.delegation_{parent,root}_draft_id are TEXT
  * specialist_delegation_edges.{parent,child}_draft_id are TEXT

The TEXT reconciliation is what lets the runtime use the UUID
``gateway_actions.id`` as a draft identifier in delegation chains
(originally declared BIGINT in 0035 — see 0037 docstring).
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


def test_specialist_reward_signals_table_exists(sync_engine):
    insp = inspect(sync_engine)
    assert "specialist_reward_signals" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("specialist_reward_signals")}
    assert {
        "id",
        "draft_id",
        "specialist",
        "signal",
        "edge_id",
        "created_at",
    }.issubset(cols)


def test_specialist_reward_signals_indexes(sync_engine):
    insp = inspect(sync_engine)
    idx_names = {i["name"] for i in insp.get_indexes("specialist_reward_signals")}
    assert "idx_reward_signals_draft" in idx_names
    assert "idx_reward_signals_specialist" in idx_names


def test_gateway_actions_delegation_draft_id_columns_are_text(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"]: c for c in insp.get_columns("gateway_actions")}
    assert str(cols["delegation_parent_draft_id"]["type"]).upper() == "TEXT"
    assert str(cols["delegation_root_draft_id"]["type"]).upper() == "TEXT"


def test_specialist_delegation_edges_draft_id_columns_are_text(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"]: c for c in insp.get_columns("specialist_delegation_edges")}
    assert str(cols["parent_draft_id"]["type"]).upper() == "TEXT"
    assert str(cols["child_draft_id"]["type"]).upper() == "TEXT"
