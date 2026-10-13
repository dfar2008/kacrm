"""0039 — schema-pin tests.

Pins:
  * specialist_delegation_acl.allowed_action_kinds rejects non-array
    JSONB values via the new CHECK constraint
  * specialist_delegation_edges.parent_draft_id / child_draft_id
    columns carry the documenting COMMENT
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from _db_url import resolve_database_url


@pytest.fixture(scope="module")
def engine():
    url = resolve_database_url()
    if not url:
        pytest.skip("No DB reachable")
    eng = create_engine(url, pool_pre_ping=True)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("DB not reachable")
    return eng


def test_acl_allowed_action_kinds_check_rejects_non_array(engine):
    """CHECK constraint blocks JSONB shapes other than 'array'."""
    with engine.begin() as conn:
        # Object — must fail
        with pytest.raises(Exception, match="check"):
            conn.execute(
                text(
                    "INSERT INTO specialist_delegation_acl "
                    "(parent_specialist, child_specialist, allowed_action_kinds) "
                    "VALUES ('check_test_parent', 'check_test_child', "
                    "CAST(:k AS JSONB))"
                ),
                {"k": json.dumps({"crm.write": True})},
            )

    with engine.begin() as conn:
        # Bare string — must fail
        with pytest.raises(Exception, match="check"):
            conn.execute(
                text(
                    "INSERT INTO specialist_delegation_acl "
                    "(parent_specialist, child_specialist, allowed_action_kinds) "
                    "VALUES ('check_test_parent', 'check_test_child', "
                    "CAST(:k AS JSONB))"
                ),
                {"k": json.dumps("crm.write")},
            )


def test_acl_allowed_action_kinds_check_accepts_array(engine):
    """Empty array AND list-of-strings both pass."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialist_delegation_acl "
                "(parent_specialist, child_specialist, allowed_action_kinds) "
                "VALUES ('check_test_p1', 'check_test_c1', "
                "CAST(:k AS JSONB)) "
                "ON CONFLICT DO NOTHING"
            ),
            {"k": json.dumps([])},
        )
        conn.execute(
            text(
                "INSERT INTO specialist_delegation_acl "
                "(parent_specialist, child_specialist, allowed_action_kinds) "
                "VALUES ('check_test_p2', 'check_test_c2', "
                "CAST(:k AS JSONB)) "
                "ON CONFLICT DO NOTHING"
            ),
            {"k": json.dumps(["crm.write", "compliance.check"])},
        )
        conn.execute(
            text(
                "DELETE FROM specialist_delegation_acl "
                "WHERE parent_specialist LIKE 'check_test_%'"
            )
        )


def test_edges_draft_id_columns_carry_no_fk_comment(engine):
    """Both draft_id columns must have a non-NULL pg_description."""
    sql = text(
        "SELECT col_description("
        "  'specialist_delegation_edges'::regclass::oid, "
        "  attnum"
        ") AS comment "
        "FROM pg_attribute "
        "WHERE attrelid = 'specialist_delegation_edges'::regclass "
        "  AND attname = :col"
    )
    with engine.begin() as conn:
        for col in ("parent_draft_id", "child_draft_id"):
            row = conn.execute(sql, {"col": col}).fetchone()
            assert row is not None, f"column {col} not found"
            assert row.comment, f"{col} missing schema COMMENT"
            assert "No FK" in row.comment, (
                f"{col} comment doesn't explain the design choice: {row.comment!r}"
            )
