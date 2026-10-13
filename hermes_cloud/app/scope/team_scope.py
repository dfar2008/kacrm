"""Resolve "which user_ids does ``caller`` see at scope X" — Phase 5.1.

Centralizes the team / org scope expansion that was previously living
inline as ``crm/routes/__init__._collect_viewable_user_ids``. Goals:

* Single helper consulted by every team-aware path (crm REST list,
  hermes-side scope ContextVar, dashboard SQL aggregate, auto_drive
  team tick, ...). Reduces drift.
* Recognizes "the user is admin" — admins implicitly hold scope='org'
* Falls back gracefully on data gaps (no department.manager_ids row,
  no permissions table yet) — never raises in the read path.

Performance: no caching at module level. cloud-side request scope can
add `functools.cache` when the same caller_id/scope tuple gets hit
multiple times in one request, but a single uncached SQL is fast (one
recursive CTE on a small ``departments`` tree).
"""
from __future__ import annotations

import json
import logging
from typing import Any, List, Literal, Set

from hermes.crm.db import DbWrapper

logger = logging.getLogger(__name__)

# Pinned literal type. v1.2 plan §3 explicitly limits to these 3 values
# (no project-team / cross-functional dynamic groupings).
ScopeKind = Literal["self", "team", "org"]


def viewable_user_ids(
    db: DbWrapper, *, caller_id: int, scope: ScopeKind,
) -> Set[int]:
    """Return the set of user_ids ``caller_id`` may read at ``scope``.

    Semantics:

    * ``'self'`` → ``{caller_id}``
    * ``'team'`` → caller + all reports under departments where caller
      is in ``manager_ids`` (recursive through ``departments.parent_id``).
      For non-managers, this collapses to ``{caller_id}`` — no error.
    * ``'org'``  → all active users IF caller is admin, else ``'team'``
      (no privilege escalation).

    Inactive users (``users.deleted=true`` or ``users.status=0``) are
    always filtered out — no leaking of off-boarded employees into a
    manager's view.

    The implementation uses ``departments.manager_ids`` jsonb array
    for recursion. ``users.report_to_ids`` is intentionally NOT used
    yet (plan v1.2 §3 — dept-based default; report_to_ids becomes a
    Phase 5+ extension).
    """
    cid = int(caller_id)

    if scope == "self":
        return {cid}

    if scope == "org":
        # Promote-admin check
        row = db.fetchone(
            "SELECT is_admin FROM users WHERE id = %s", (cid,),
        )
        if not row or not row.get("is_admin"):
            # Auto-downgrade — non-admin can't elevate to org
            scope = "team"
        else:
            rows = db.fetchall(
                "SELECT id FROM users "
                " WHERE deleted = FALSE "
                "   AND (status IS NULL OR status = 1)",
            ) or []
            return {int(r["id"]) for r in rows if r.get("id") is not None}

    # scope == "team"
    try:
        rows = db.fetchall(
            """
            WITH RECURSIVE managed_dept(id) AS (
                SELECT d.id FROM departments d
                 WHERE d.manager_ids @> %s::jsonb
                UNION
                SELECT child.id FROM departments child
                 JOIN managed_dept md ON child.parent_id = md.id
            )
            SELECT DISTINCT u.id
              FROM users u
             WHERE u.deleted = FALSE
               AND (u.status IS NULL OR u.status = 1)
               AND (u.department_id IN (SELECT id FROM managed_dept)
                    OR u.id = %s)
            """,
            (json.dumps([cid]), cid),
        ) or []
        return {int(r["id"]) for r in rows if r.get("id") is not None}
    except Exception:
        logger.exception(
            "viewable_user_ids team-scope query failed caller_id=%s; "
            "falling back to self-only",
            cid,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return {cid}


# Backward-compat shim for callers that already migrated to the
# private helper in crm.routes. Returns a list (matching the old
# signature) sorted for deterministic test output.
def viewable_user_ids_list(
    db: DbWrapper, *, caller_id: int, scope: ScopeKind = "team",
) -> List[int]:
    """List variant of :func:`viewable_user_ids`, sorted."""
    return sorted(viewable_user_ids(db, caller_id=caller_id, scope=scope))
