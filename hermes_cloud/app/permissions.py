"""Unified permission engine for Hermes Cloud (Hermes-native surfaces).

The CRM package already ships a full permission checker in
:mod:`hermes.crm.permission` — this module wraps it for the
call-sites where:

* The module code is fixed at compile time (``/api/agents``,
  ``/api/mail`` …) rather than read from a URL path parameter;
* The caller authenticates via the dashboard cookie / bearer scheme in
  :mod:`hermes_cloud.app.auth` rather than the CRM's session cookie;
* The response shape needs a "grants list" flat enough for the SPA to
  include/exclude nav items without doing per-route round-trips.

All three layers run on the SAME underlying tables
(``collections`` × ``permissions`` × ``role_permissions``). Admin users
(``users.is_admin = TRUE``) short-circuit to TRUE everywhere; the CRM
checker already handles that.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from hermes_cloud.app.auth import require_dashboard_auth
from hermes_cloud.app.config import AppConfig
from hermes.crm.db import db_session
from hermes.crm.permission import Permission


# ---------------------------------------------------------------------------
# Module name conventions
# ---------------------------------------------------------------------------
#
# Three buckets live in ``collections``:
#
# * ``kind='crm'``    — accounts/contacts/leads/... (kacrm ports)
# * ``kind='hermes'`` — agents/chat/mail/touchpoints/...
# * ``kind='admin'``  — admin_users/admin_departments/admin_roles
#
# The role-permission UI groups its matrix tabs by ``kind``.


# ---------------------------------------------------------------------------
# Auth glue
# ---------------------------------------------------------------------------

def _current_user_id(request: Request) -> int:
    cfg = getattr(request.app.state, "config", None) or AppConfig()
    uid, _ = require_dashboard_auth(request, cfg)
    try:
        return int(uid)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="user id must be numeric")


# ---------------------------------------------------------------------------
# Check helpers
# ---------------------------------------------------------------------------

def check_permission(user_id: int, module_code: str, action: str) -> bool:
    """Return whether *user_id* may perform *action* on *module_code*.

    Delegates to :class:`hermes.crm.permission.Permission` so
    every rule (``is_admin`` bypass, ``globalview`` fallback, derived
    ``massupdate`` logic …) stays in one place.
    """
    with db_session() as db:
        return Permission(db).check_permission_by_module_name(
            user_id, module_code, action,
        )


# ---------------------------------------------------------------------------
# /api/me/permissions payload
# ---------------------------------------------------------------------------

def collect_user_grants(user_id: int) -> Dict[str, Any]:
    """Return a structured permission summary for *user_id*.

    Shape::

        {
          "is_admin": bool,
          "grants": ["accounts.index", "contacts.view", ...],  # flat, module.action
          "modules": [
             {"id": 64, "name": "agents", "title": "Agents",
              "kind": "hermes", "description": "...",
              "actions": ["index","view","create","update","delete"]},
             ...
          ],
        }

    * Admins get ``is_admin=True`` AND the fully-enumerated ``grants``
      list (one entry per known module×permission pair), so the frontend
      can trust ``grants.includes("x")`` alone — no special-case needed
      for ``*``.
    * Non-admins with no role_id get an empty grants list.
    """
    with db_session() as db:
        user = db.fetchone(
            "SELECT is_admin, role_id FROM users WHERE id = %s", (user_id,)
        )
        if not user:
            return {"is_admin": False, "grants": [], "modules": []}

        module_rows = db.fetchall(
            "SELECT id, name, title, kind, description FROM collections"
        )
        modules = [
            {
                "id": int(m["id"]),
                "name": m["name"],
                "title": m["title"],
                "kind": m.get("kind") or "crm",
                "description": m.get("description"),
            }
            for m in module_rows
        ]

        perm_rows = db.fetchall("SELECT id, code FROM permissions")
        all_perm_codes = [p["code"] for p in perm_rows]

        is_admin = bool(user.get("is_admin"))
        if is_admin:
            grants = [f"{m['name']}.{code}" for m in modules for code in all_perm_codes]
            return {"is_admin": True, "grants": grants, "modules": modules}

        role_id = user.get("role_id")
        if not role_id:
            return {"is_admin": False, "grants": [], "modules": modules}

        rows = db.fetchall(
            """
            SELECT c.name AS module, p.code AS action
              FROM role_permissions rp
              JOIN collections c ON c.id = rp.module_id
              JOIN permissions p ON p.id = rp.permission_id
             WHERE rp.role_id = %s
               AND rp.has_permission = TRUE
            """,
            (role_id,),
        )
        grants = [f"{r['module']}.{r['action']}" for r in rows]
        return {"is_admin": False, "grants": grants, "modules": modules}


# ---------------------------------------------------------------------------
# Profile quota resolver
# ---------------------------------------------------------------------------

#: Absolute-last-resort fallback when neither the user nor their role has a
#: value set and the user isn't an admin. Matches the rule that "every user
#: gets at least their auto-provisioned default profile" — so a cap of 1 is
#: the gentlest default we can land on.
DEFAULT_PROFILE_QUOTA = 1


def resolve_profile_quota(user_id: int | str) -> Optional[int]:
    """Return the effective profile cap for *user_id*.

    Resolution order:

    1. ``users.is_admin`` → ``None`` (unlimited)
    2. ``users.profile_quota`` (per-user override) if not NULL
    3. ``roles.profile_quota`` (role default) if not NULL
    4. :data:`DEFAULT_PROFILE_QUOTA` (= 1)

    Returns ``None`` for "unlimited"; the caller should treat that the
    same as "don't pass a cap to ``profile_store.create_profile``".
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return DEFAULT_PROFILE_QUOTA
    with db_session() as db:
        row = db.fetchone(
            """
            SELECT u.is_admin,
                   u.profile_quota AS user_quota,
                   r.profile_quota AS role_quota
              FROM users u
              LEFT JOIN roles r ON r.id = u.role_id
             WHERE u.id = %s
            """,
            (uid,),
        )
    if not row:
        return DEFAULT_PROFILE_QUOTA
    if row.get("is_admin"):
        return None
    uq = row.get("user_quota")
    if uq is not None:
        return int(uq)
    rq = row.get("role_quota")
    if rq is not None:
        return int(rq)
    return DEFAULT_PROFILE_QUOTA


__all__ = [
    "check_permission",
    "collect_user_grants",
    "resolve_profile_quota",
    "DEFAULT_PROFILE_QUOTA",
]
