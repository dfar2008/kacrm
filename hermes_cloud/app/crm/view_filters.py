"""Dynamic-value resolution for custom view filters.

A saved view stores filter conditions like
``{"field": "owner_by", "operator": "eq", "value": "current"}``. Some
values are *dynamic placeholders* that must be resolved per request — by
the calling user and the current time — so one shared (public) view does
the right thing for everyone:

  * ``"current"``        → the caller's user id.
  * ``"team"``           → the caller + subordinates (list of ids); the
                           operator is forced to ``in``.
  * date tokens on the value:
      - ``"today"``        → [start_of_today, end_of_today]
      - ``"this_week"``    → [start_of_week(Mon), end_of_week(Sun)]
      - ``"last_n_days:N"``→ [now - N days, now]
    Each date range expands to **two** conditions (``gte`` + ``lte``) on
    the same field, because ``list_query`` has no ``between`` operator.

Anything not recognised is passed through untouched (literal value).

This lives in ``hermes_cloud`` (not the ``hermes`` submodule) so we apply
it in the route layer before calling ``ListQuery.set_filters`` — no
upstream/list_query edit needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from hermes_cloud.app.scope import viewable_user_ids


_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(_DATE_FMT)


def _expand_date_token(field: str, token: str, now: datetime) -> Optional[List[Dict[str, Any]]]:
    """Return gte/lte condition pair for a date token, or None if *token*
    is not a recognised date token."""
    today_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    if token == "today":
        end = today_start + timedelta(days=1) - timedelta(seconds=1)
        return [
            {"field": field, "operator": "gte", "value": _fmt(today_start)},
            {"field": field, "operator": "lte", "value": _fmt(end)},
        ]
    if token == "this_week":
        # Monday as week start.
        week_start = today_start - timedelta(days=today_start.weekday())
        week_end = week_start + timedelta(days=7) - timedelta(seconds=1)
        return [
            {"field": field, "operator": "gte", "value": _fmt(week_start)},
            {"field": field, "operator": "lte", "value": _fmt(week_end)},
        ]
    if token.startswith("last_n_days:"):
        try:
            n = int(token.split(":", 1)[1])
        except (ValueError, IndexError):
            return None
        start = now - timedelta(days=n)
        return [
            {"field": field, "operator": "gte", "value": _fmt(start)},
            {"field": field, "operator": "lte", "value": _fmt(now)},
        ]
    return None


def resolve_dynamic_filters(
    db: Any,
    filters: Any,
    *,
    user: Dict[str, Any],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Resolve dynamic placeholders in *filters* into concrete conditions.

    ``filters`` is the raw view filter list; returns a new list (possibly
    longer, since date tokens expand to two conditions). Unknown values
    pass through unchanged.
    """
    if not isinstance(filters, list):
        return []
    if now is None:
        now = datetime.now(tz=timezone.utc)
    caller_id = int(user.get("id") or 0)

    out: List[Dict[str, Any]] = []
    for cond in filters:
        if not isinstance(cond, dict):
            continue
        field = cond.get("field")
        operator = cond.get("operator", "eq")
        value = cond.get("value")
        if not field:
            continue

        if value == "current":
            out.append({"field": field, "operator": operator, "value": caller_id})
            continue
        if value == "team":
            try:
                ids = sorted(viewable_user_ids(db, caller_id=caller_id, scope="team"))
            except Exception:
                ids = [caller_id]
            out.append({"field": field, "operator": "in", "value": ids})
            continue
        if isinstance(value, str):
            expanded = _expand_date_token(field, value, now)
            if expanded is not None:
                out.extend(expanded)
                continue
        # Literal — pass through.
        out.append({"field": field, "operator": operator, "value": value})
    return out
