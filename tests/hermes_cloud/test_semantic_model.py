"""Unit tests for semantic.model dataclasses."""
from __future__ import annotations

import pytest

from hermes_cloud.app.dashboards.semantic.model import (
    Dimension,
    Entity,
    Measure,
    DetailColumns,
    RowFilter,
)


def _empty_filter() -> RowFilter:
    return RowFilter(self_="1=1", team="1=1", org="1=1")


def test_row_filter_for_scope():
    rf = RowFilter(self_="o = :caller_id",
                   team="o IN team(:caller_id)",
                   org="1=1")
    assert rf.for_scope("self") == "o = :caller_id"
    assert rf.for_scope("team") == "o IN team(:caller_id)"
    assert rf.for_scope("org") == "1=1"
    with pytest.raises(KeyError):
        rf.for_scope("planet")


def test_entity_dimension_and_measure_lookup():
    e = Entity(
        name="customer",
        source_view="v_customer",
        row_filter=_empty_filter(),
        dimensions=(Dimension(name="stage", type="enum",
                              values=("mql", "contract")),),
        measures=(Measure(name="count", type="count"),
                  Measure(name="arr", type="sum", sql="amount",
                          requires_grant="see_revenue"),),
        detail_columns=DetailColumns(default=("id", "name"),
                                     optional=("amount",)),
    )
    assert e.dimension("stage").type == "enum"
    assert e.measure("arr").requires_grant == "see_revenue"
    assert e.has_measure("arr") is True
    assert e.has_measure("missing") is False
    with pytest.raises(KeyError):
        e.dimension("ghost")
    with pytest.raises(KeyError):
        e.measure("ghost")


def test_detail_columns_all_union():
    dc = DetailColumns(default=("a", "b"), optional=("c", "d"))
    assert dc.all() == {"a", "b", "c", "d"}


def test_entity_frozen_immutability():
    e = Entity(name="x", source_view="v", row_filter=_empty_filter(),
               dimensions=(), measures=(),
               detail_columns=DetailColumns(default=(), optional=()))
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.name = "y"  # type: ignore[misc]


import dataclasses  # noqa: E402  (kept here for the test above)
