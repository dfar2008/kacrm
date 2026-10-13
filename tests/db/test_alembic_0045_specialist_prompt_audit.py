"""0045 — schema-pin tests for specialist_prompt_audit.

Pins:
  * Table exists with all 8 columns
  * called_from CHECK constraint allows the two valid values
  * Three indexes (unique dedup, lookup, hash)

See docs/2026-05-02-v33-rectification-plan.md §五 v3.3.2b.
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


def test_table_exists_with_expected_columns(sync_engine):
    insp = inspect(sync_engine)
    cols = {c["name"] for c in insp.get_columns("specialist_prompt_audit")}
    expected = {
        "id",
        "profile_id",
        "specialist_name",
        "prompt_hash",
        "recorded_at",
        "called_from",
        "draft_id",
        "metadata",
    }
    assert expected.issubset(cols)


def test_indexes_exist(sync_engine):
    sql = text(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename = 'specialist_prompt_audit'"
    )
    with sync_engine.connect() as conn:
        names = {r[0] for r in conn.execute(sql).fetchall()}
    assert "uq_specialist_prompt_audit_dedup" in names
    assert "idx_specialist_prompt_audit_lookup" in names
    assert "idx_specialist_prompt_audit_hash" in names


def test_called_from_check_constraint(sync_engine):
    """Inserting an unknown called_from value must fail."""
    with sync_engine.connect() as conn:
        with pytest.raises(Exception):
            with conn.begin():
                conn.execute(
                    text(
                        "INSERT INTO specialist_prompt_audit "
                        "(profile_id, specialist_name, prompt_hash, called_from) "
                        "VALUES ('test-bad', 'x', 'x', 'banana')"
                    )
                )
    # Cleanup any partial state
    with sync_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM specialist_prompt_audit WHERE profile_id = 'test-bad'")
        )


def test_valid_called_from_values_accepted(sync_engine):
    with sync_engine.begin() as conn:
        for v in ("root_invocation", "child_spawn"):
            conn.execute(
                text(
                    "INSERT INTO specialist_prompt_audit "
                    "(profile_id, specialist_name, prompt_hash, called_from) "
                    "VALUES (:p, 'x', :h, :v)"
                ),
                {"p": f"test-pin-{v}", "h": v, "v": v},
            )
    with sync_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM specialist_prompt_audit WHERE profile_id LIKE 'test-pin-%'")
        )
