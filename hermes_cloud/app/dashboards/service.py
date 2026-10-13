"""Dashboards service layer — visibility checks, fork, role resolution.

Pure functions over store.* objects; no transport, no DB I/O of its own
beyond what fork_tab needs.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import store


logger = logging.getLogger(__name__)


class NotAuthorized(Exception): ...


# ---- visibility ------------------------------------------------------------


def can_view_tab(
    tab: store.DashboardTab,
    *,
    viewer_id: int,
    viewer_dept_id: Optional[int] = None,
) -> bool:
    if tab.owner_user_id == viewer_id:
        return True
    if tab.visibility != "shared":
        return False
    sw = tab.shared_with or {}
    if sw.get("everyone"):
        return True
    if viewer_id in (sw.get("users") or []):
        return True
    if viewer_dept_id is not None and viewer_dept_id in (sw.get("departments") or []):
        return True
    # NOTE: spec lists "teams" as a sharing grain but hermes-cloud has no
    # teams table; we currently match on department instead. Keep the key
    # in shared_with payloads forward-compatible — it's just unused today.
    return False


def can_edit_tab(tab: store.DashboardTab, *, viewer_id: int) -> bool:
    """Only the tab owner can edit. Sharing is read-only — recipients fork."""
    return tab.owner_user_id == viewer_id


# ---- role resolution -------------------------------------------------------


def resolve_role(*, is_admin: bool, has_reports: bool) -> str:
    """Map (is_admin, has_reports) -> 'admin' | 'manager' | 'employee'.

    has_reports means 'someone reports to this user' (i.e. caller appears
    in another user's report_to_ids JSONB array).
    """
    if is_admin:
        return "admin"
    if has_reports:
        return "manager"
    return "employee"


# ---- fork ------------------------------------------------------------------


def fork_tab(db, *, source_tab_id, new_owner_id: int) -> store.DashboardTab:
    """Copy a tab + its charts to ``new_owner_id``. Preserves ``preset_key``
    for traceability; sets ``is_system_preset=False`` on the copy.
    """
    src = store.get_tab(db, source_tab_id)
    if not src:
        raise ValueError(f"tab {source_tab_id} not found")
    new_tab = store.create_tab(
        db, owner_user_id=new_owner_id, title=src.title,
        is_system_preset=False, preset_key=src.preset_key,
    )
    for c in store.list_charts(db, tab_id=src.id):
        store.create_chart(
            db, tab_id=new_tab.id, title=c.title,
            dsl=c.dsl, position=c.position,
        )
    return new_tab
