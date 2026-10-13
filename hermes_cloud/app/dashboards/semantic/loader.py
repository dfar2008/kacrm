"""YAML → semantic-layer Entity loader.

The on-disk schema is intentionally lenient: missing optional fields default
to empty tuples, but ``entity`` / ``source_view`` / ``row_filter.{self,team,org}``
/ ``detail_columns.{default,optional}`` are required and yield clear errors
when missing.

Lists in YAML become tuples in dataclasses (frozen → must be hashable).
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml

from .model import (
    Dimension,
    Entity,
    Measure,
    DetailColumns,
    RowFilter,
)


class SemanticLoadError(Exception):
    """Raised on malformed entity YAML."""


def _require(d: dict, key: str, ctx: str) -> Any:
    if key not in d:
        raise SemanticLoadError(f"{ctx}: missing required key {key!r}")
    return d[key]


def _build_dim(d: dict, ctx: str) -> Dimension:
    name = _require(d, "name", f"{ctx}.dimension")
    return Dimension(
        name=name,
        type=_require(d, "type", f"{ctx}.dimension[{name}]"),
        values=tuple(d["values"]) if d.get("values") is not None else None,
        granularities=tuple(d["granularities"]) if d.get("granularities") is not None else None,
        references=d.get("references"),
    )


def _build_measure(d: dict, ctx: str) -> Measure:
    name = _require(d, "name", f"{ctx}.measure")
    return Measure(
        name=name,
        type=_require(d, "type", f"{ctx}.measure[{name}]"),
        sql=d.get("sql"),
        where=d.get("where"),
        percentile=d.get("percentile"),
        requires_grant=d.get("requires_grant"),
    )


def load_entity_yaml(path: pathlib.Path) -> Entity:
    """Parse one entity YAML file into an Entity dataclass."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise SemanticLoadError(f"{path}: top-level must be a mapping")

    ctx = str(path)
    name = _require(raw, "entity", ctx)
    source_view = _require(raw, "source_view", ctx)

    rf_raw = _require(raw, "row_filter", ctx)
    if not isinstance(rf_raw, dict):
        raise SemanticLoadError(f"{ctx}: row_filter must be a mapping")
    rf = RowFilter(
        self_=_require(rf_raw, "self", f"{ctx}.row_filter"),
        team=_require(rf_raw, "team", f"{ctx}.row_filter"),
        org=_require(rf_raw, "org", f"{ctx}.row_filter"),
    )

    dims = tuple(_build_dim(d, ctx) for d in raw.get("dimensions", []) or [])
    meas = tuple(_build_measure(m, ctx) for m in raw.get("measures", []) or [])

    dc_raw = _require(raw, "detail_columns", ctx)
    dc = DetailColumns(
        default=tuple(dc_raw.get("default") or ()),
        optional=tuple(dc_raw.get("optional") or ()),
    )

    return Entity(
        name=name,
        source_view=source_view,
        row_filter=rf,
        dimensions=dims,
        measures=meas,
        detail_columns=dc,
        module_table=raw.get("module_table"),
    )
