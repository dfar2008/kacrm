"""DSL Pydantic models — the LLM emits JSON conforming to these (spec §4.e).

Validation is intentionally minimal at this layer (just shape + enums);
the compiler does the entity/measure/scope/grant validation since it
knows the registry.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


ChartType = Literal[
    "line", "bar", "stacked_bar", "horizontal_bar",
    "pie", "donut",
    "table", "report_table",
    "metric", "funnel", "composite", "text",
]
Scope = Literal["self", "team", "org"]
FilterOp = Literal[
    "eq", "neq", "in", "nin", "gt", "gte", "lt", "lte", "between",
    "is_null", "is_not_null",
]
Aggregation = Literal[
    "sum", "count", "avg", "min", "max",
    "median", "p25", "p50", "p75", "p90", "p95", "p99",
]


class DSLMeasure(BaseModel):
    name: str
    agg: Optional[Aggregation] = None  # override aggregation; default per measure type


class DSLDimension(BaseModel):
    name: str
    granularity: Optional[str] = None  # day/week/month/quarter/year for time dims


class DSLFilter(BaseModel):
    dim: str
    op: FilterOp
    value: Any


class DSLOrderBy(BaseModel):
    measure: Optional[str] = None
    dimension: Optional[str] = None
    dir: Literal["asc", "desc"] = "asc"


class DSLChart(BaseModel):
    type: ChartType
    title: str
    x: Optional[str] = None
    y: Optional[str] = None
    # Second measure (composite: bar=y + line=y2) OR second dimension
    # (report_table: row=x + col=y2 + cell=y).
    y2: Optional[str] = None
    series: Optional[str] = None  # group dim for stacked / multi-series
    # Inline markdown for chart.type == "text"; ignored otherwise.
    text: Optional[str] = None


class DSLQuery(BaseModel):
    entity: str
    scope: Scope
    measures: List[DSLMeasure]
    dimensions: List[DSLDimension] = Field(default_factory=list)
    filters: List[DSLFilter] = Field(default_factory=list)
    order_by: List[DSLOrderBy] = Field(default_factory=list)
    limit: int = Field(default=1000, ge=1, le=10000)
    chart: DSLChart
