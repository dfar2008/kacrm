"""DB read/write for dashboard_tabs + dashboard_charts.

Mirrors hermes_cloud/app/decisions/store.py shape: dataclasses + plain
functions over DbWrapper, no ORM. SQL uses %s positional placeholders
with tuple args (per RECON note R-1).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import uuid
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class DashboardTab:
    id: uuid.UUID
    owner_user_id: int
    title: str
    position: int
    visibility: str
    shared_with: Dict[str, Any]
    is_system_preset: bool
    preset_key: Optional[str]
    # Bilingual title for preset-seeded tabs, ``{locale: text}``.
    # NULL for user-created tabs and for preset tabs the user manually
    # renamed (in which case ``title`` is the canonical display). The
    # routes layer resolves the per-request locale before serializing.
    title_i18n: Optional[Dict[str, str]]
    created_at: Optional[dt.datetime]
    updated_at: Optional[dt.datetime]


@dataclasses.dataclass
class DashboardChart:
    id: uuid.UUID
    tab_id: uuid.UUID
    position: int
    title: str
    dsl: Dict[str, Any]
    grid_w: int
    grid_h: int
    source_prompt_message_id: Optional[uuid.UUID]
    created_at: Optional[dt.datetime]
    updated_at: Optional[dt.datetime]


# Default card footprint per chart type. Metric / text take a single
# cell; visualizations need room for axes + legend so they default to
# 2x2. The DB CHECK clamps each axis to [1, 4].
_DEFAULT_GRID: Dict[str, tuple[int, int]] = {
    "metric": (1, 1),
    "text":   (2, 1),
    "table":  (2, 2),
    "report_table": (2, 2),
}


def default_grid_for(chart_type: Optional[str]) -> tuple[int, int]:
    return _DEFAULT_GRID.get((chart_type or "").lower(), (2, 2))


# ---- row -> dataclass ------------------------------------------------------


def _row_to_tab(r: Dict[str, Any]) -> DashboardTab:
    sw = r.get("shared_with") or {}
    if isinstance(sw, str):
        sw = json.loads(sw) if sw else {}
    title_i18n = r.get("title_i18n")
    if isinstance(title_i18n, str):
        title_i18n = json.loads(title_i18n) if title_i18n else None
    if title_i18n is not None and not isinstance(title_i18n, dict):
        title_i18n = None
    return DashboardTab(
        id=r["id"], owner_user_id=int(r["owner_user_id"]),
        title=r["title"], position=int(r["position"] or 0),
        visibility=r["visibility"], shared_with=sw,
        is_system_preset=bool(r["is_system_preset"]),
        preset_key=r.get("preset_key"),
        title_i18n=title_i18n,
        created_at=r.get("created_at"), updated_at=r.get("updated_at"),
    )


def _row_to_chart(r: Dict[str, Any]) -> DashboardChart:
    dsl = r.get("dsl") or {}
    if isinstance(dsl, str):
        dsl = json.loads(dsl) if dsl else {}
    return DashboardChart(
        id=r["id"], tab_id=r["tab_id"],
        position=int(r["position"] or 0),
        title=r["title"], dsl=dsl,
        grid_w=int(r.get("grid_w") or 2),
        grid_h=int(r.get("grid_h") or 2),
        source_prompt_message_id=r.get("source_prompt_message_id"),
        created_at=r.get("created_at"), updated_at=r.get("updated_at"),
    )


# ---- tabs ------------------------------------------------------------------


def create_tab(
    db, *,
    owner_user_id: int,
    title: str,
    position: int = 0,
    visibility: str = "private",
    shared_with: Optional[Dict[str, Any]] = None,
    is_system_preset: bool = False,
    preset_key: Optional[str] = None,
    title_i18n: Optional[Dict[str, str]] = None,
) -> DashboardTab:
    row = db.fetchone(
        """
        INSERT INTO dashboard_tabs
          (owner_user_id, title, position, visibility, shared_with,
           is_system_preset, preset_key, title_i18n)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
        RETURNING *
        """,
        (
            owner_user_id, title, position, visibility,
            json.dumps(shared_with or {}),
            is_system_preset, preset_key,
            json.dumps(title_i18n) if title_i18n is not None else None,
        ),
    )
    return _row_to_tab(row)


def get_tab(db, tab_id: uuid.UUID) -> Optional[DashboardTab]:
    row = db.fetchone(
        "SELECT * FROM dashboard_tabs WHERE id = %s",
        (str(tab_id),),
    )
    return _row_to_tab(row) if row else None


def list_tabs_for_user(db, *, user_id: int) -> List[DashboardTab]:
    """Return tabs owned by user OR shared with them per shared_with rules.

    Sharing predicate (matches spec §5.d):
      visibility='shared' AND (
        shared_with.everyone=true
        OR shared_with.users contains user_id
        OR shared_with.teams contains user's team_id (NULL — see note)
        OR shared_with.departments contains user's department_id
      )

    Note: hermes-cloud has no teams table; team-grain sharing currently
    matches by department_id (closest analog). Plan §R-4 acknowledges this.
    """
    rows = db.fetchall(
        """
        SELECT t.* FROM dashboard_tabs t
        WHERE t.owner_user_id = %s
           OR (
             t.visibility = 'shared' AND (
                COALESCE((t.shared_with->>'everyone')::boolean, false) = true
             OR t.shared_with->'users' @> to_jsonb(%s::int)
             OR t.shared_with->'departments' @> (
                  SELECT to_jsonb(department_id) FROM users WHERE id = %s
                )
             )
           )
        ORDER BY t.position, t.created_at
        """,
        (user_id, user_id, user_id),
    )
    return [_row_to_tab(r) for r in rows]


def update_tab(db, tab_id: uuid.UUID, **fields) -> Optional[DashboardTab]:
    if not fields:
        return get_tab(db, tab_id)
    parts: List[str] = []
    values: List[Any] = []
    for k, v in fields.items():
        if k == "shared_with" and not isinstance(v, str):
            parts.append(f"{k} = %s::jsonb")
            values.append(json.dumps(v))
        elif k == "title_i18n":
            # ``None`` legitimately clears the i18n override (user
            # renamed a preset tab → their text wins). ``dict`` →
            # JSONB. String passthrough kept for completeness.
            parts.append(f"{k} = %s::jsonb")
            values.append(
                None if v is None
                else (v if isinstance(v, str) else json.dumps(v))
            )
        else:
            parts.append(f"{k} = %s")
            values.append(v)
    values.append(str(tab_id))
    row = db.fetchone(
        f"UPDATE dashboard_tabs SET {', '.join(parts)}, updated_at = now() "
        f"WHERE id = %s RETURNING *",
        tuple(values),
    )
    return _row_to_tab(row) if row else None


def delete_tab(db, tab_id: uuid.UUID) -> None:
    db.execute("DELETE FROM dashboard_tabs WHERE id = %s", (str(tab_id),))


# ---- charts ----------------------------------------------------------------


def create_chart(
    db, *,
    tab_id: uuid.UUID,
    title: str,
    dsl: Dict[str, Any],
    position: int = 0,
    source_prompt_message_id: Optional[uuid.UUID] = None,
    grid_w: Optional[int] = None,
    grid_h: Optional[int] = None,
) -> DashboardChart:
    if grid_w is None or grid_h is None:
        ct = (dsl.get("chart") or {}).get("type") if isinstance(dsl, dict) else None
        default_w, default_h = default_grid_for(ct)
        grid_w = grid_w if grid_w is not None else default_w
        grid_h = grid_h if grid_h is not None else default_h
    row = db.fetchone(
        """
        INSERT INTO dashboard_charts
          (tab_id, title, dsl, position, source_prompt_message_id,
           grid_w, grid_h)
        VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            str(tab_id), title, json.dumps(dsl), position,
            str(source_prompt_message_id) if source_prompt_message_id else None,
            int(grid_w), int(grid_h),
        ),
    )
    return _row_to_chart(row)


def get_chart(db, chart_id: uuid.UUID) -> Optional[DashboardChart]:
    row = db.fetchone(
        "SELECT * FROM dashboard_charts WHERE id = %s",
        (str(chart_id),),
    )
    return _row_to_chart(row) if row else None


def list_charts(db, *, tab_id: uuid.UUID) -> List[DashboardChart]:
    rows = db.fetchall(
        "SELECT * FROM dashboard_charts WHERE tab_id = %s ORDER BY position",
        (str(tab_id),),
    )
    return [_row_to_chart(r) for r in rows]


def update_chart(db, chart_id: uuid.UUID, **fields) -> Optional[DashboardChart]:
    if not fields:
        return get_chart(db, chart_id)
    parts: List[str] = []
    values: List[Any] = []
    for k, v in fields.items():
        if k == "dsl" and not isinstance(v, str):
            parts.append(f"{k} = %s::jsonb")
            values.append(json.dumps(v))
        else:
            parts.append(f"{k} = %s")
            values.append(v)
    values.append(str(chart_id))
    row = db.fetchone(
        f"UPDATE dashboard_charts SET {', '.join(parts)}, updated_at = now() "
        f"WHERE id = %s RETURNING *",
        tuple(values),
    )
    return _row_to_chart(row) if row else None


def delete_chart(db, chart_id: uuid.UUID) -> None:
    db.execute("DELETE FROM dashboard_charts WHERE id = %s", (str(chart_id),))
