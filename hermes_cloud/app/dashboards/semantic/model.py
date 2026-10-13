"""Semantic-layer dataclasses (spec §四).

These objects are the in-memory representation of one entity's YAML file.
Compiler + DSL validator consume them; nothing here knows about SQL.

Frozen dataclasses give us free equality + hashability, which makes the
registry comparable in tests.
"""
from __future__ import annotations

import dataclasses
from typing import List, Literal, Optional


DimensionType = Literal["string", "enum", "time", "ref", "int", "float", "bool"]
MeasureType = Literal["count", "sum", "avg", "median", "percentile"]


@dataclasses.dataclass(frozen=True)
class RowFilter:
    """SQL fragments for the three scopes.

    Each is a WHERE-clause snippet that must reference ``:caller_id``
    (the compiler rewrites this to a parameterized placeholder).
    Use ``"1=1"`` for entities with no scope distinction (e.g. ``user``).
    """

    self_: str
    team: str
    org: str

    def for_scope(self, scope: str) -> str:
        return {"self": self.self_, "team": self.team, "org": self.org}[scope]


@dataclasses.dataclass(frozen=True)
class Dimension:
    name: str
    type: DimensionType
    # ``values`` populated for enum dims; ``granularities`` for time dims;
    # ``references`` for ref-typed dims (e.g. owner_user_id -> user).
    values: Optional[tuple[str, ...]] = None
    granularities: Optional[tuple[str, ...]] = None
    references: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class Measure:
    name: str
    type: MeasureType
    sql: Optional[str] = None  # the column / expression being aggregated
    where: Optional[str] = None  # additional row filter for this measure
    percentile: Optional[float] = None  # required when type='percentile'
    requires_grant: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class DetailColumns:
    """Column whitelist for drill-to-detail (spec §6.g).

    ``default`` columns are always shown; ``optional`` may be added by the
    user via the modal's "+列" picker, subject to grant inheritance rules
    enforced in compiler.compile_drill.
    """

    default: tuple[str, ...]
    optional: tuple[str, ...]

    def all(self) -> set[str]:
        return set(self.default) | set(self.optional)


@dataclasses.dataclass(frozen=True)
class Entity:
    """One semantic-layer entity (e.g. ``customer``).

    ``module_table`` (optional): if set, this entity maps to a CRM module
    table whose columns can grow at runtime via the schema-evo field
    builder. Triggers two things at registry refresh time:
      1. The matching ``v_dashboard_<name>`` view is rebuilt from the
         actual ``information_schema.columns`` of ``module_table``.
      2. ``module_fields`` rows for this module are loaded and added as
         additional dimensions (auto-typed from field_type) and as
         optional detail_columns.
    """

    name: str
    source_view: str  # may be a real view OR a table name, compiler doesn't care
    row_filter: RowFilter
    dimensions: tuple[Dimension, ...]
    measures: tuple[Measure, ...]
    detail_columns: DetailColumns
    module_table: str | None = None

    def dimension(self, name: str) -> Dimension:
        for d in self.dimensions:
            if d.name == name:
                return d
        raise KeyError(f"dimension {name!r} not in entity {self.name!r}")

    def measure(self, name: str) -> Measure:
        for m in self.measures:
            if m.name == name:
                return m
        raise KeyError(f"measure {name!r} not in entity {self.name!r}")

    def has_dimension(self, name: str) -> bool:
        return any(d.name == name for d in self.dimensions)

    def has_measure(self, name: str) -> bool:
        return any(m.name == name for m in self.measures)
