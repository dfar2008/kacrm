"""Unit tests for crm_search's owner="me" surface + filter-field guard.

PG-free: exercises the pure schema/validation logic added to close the
"agent guesses the current user's name" bug (a CRM agent that had no notion
of the logged-in user filtered by a remembered nickname "丁总", matching
nothing). These assert the agent-facing contract, not DB behavior.
"""
from __future__ import annotations

import pytest

from hermes.tools.crm.dispatch import (
    _assert_search_filter_fields,
    _unified_search_tool,
)


def test_search_tool_exposes_owner_and_filters() -> None:
    spec = _unified_search_tool(["tasks", "leads", "accounts"])
    props = spec.input_schema["properties"]
    # New first-class ownership + structured-filter surface.
    assert "owner" in props, "crm_search must expose an `owner` param"
    assert props["owner"]["enum"] == ["me"]
    assert "filters" in props and props["filters"]["type"] == "array"


def test_search_description_warns_name_only_and_owner_me() -> None:
    desc = _unified_search_tool(["tasks"]).description
    # The description must tell the agent search is name-only (no field:value
    # DSL) and how to scope to itself — the two facts whose absence let it
    # fabricate `owner_by:丁总`.
    assert 'owner="me"' in desc
    assert "NOT a query language" in desc
    search_desc = _unified_search_tool(["tasks"]).input_schema["properties"]["search"][
        "description"
    ]
    assert "NAME only" in search_desc


class _StubDb:
    """Minimal db double: returns a fixed information_schema column set."""

    def __init__(self, columns: list[str]) -> None:
        self._columns = columns

    def fetchall(self, sql: str, params):  # noqa: ANN001 - test double
        assert "information_schema.columns" in sql
        return [{"column_name": c} for c in self._columns]


def test_filter_guard_accepts_real_columns() -> None:
    db = _StubDb(["tasks_id", "tasks_name", "status", "owner_by", "created_by"])
    # Should not raise.
    _assert_search_filter_fields(
        db,
        "tasks",
        [{"field": "status", "operator": "eq", "value": "open"}],
    )


def test_filter_guard_rejects_fabricated_field() -> None:
    db = _StubDb(["tasks_id", "tasks_name", "status", "owner_by"])
    with pytest.raises(ValueError) as exc:
        _assert_search_filter_fields(
            db,
            "tasks",
            [{"field": "assignee_nickname", "operator": "eq", "value": "丁总"}],
        )
    msg = str(exc.value)
    # Loud, self-correcting: names the bad field AND lists valid ones.
    assert "assignee_nickname" in msg
    assert "status" in msg  # valid-field list is surfaced


def test_filter_guard_strips_relation_module_suffix() -> None:
    db = _StubDb(["owner_by", "tasks_name"])
    # ``field#moduleId`` relation filters validate on the bare column.
    _assert_search_filter_fields(
        db, "tasks", [{"field": "owner_by#7", "operator": "eq", "value": 3}]
    )
