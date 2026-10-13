"""Bridge identity — single user across hermes + CRM.

Phase 1d of the hermes × CRM merge (plan §6.1). The bridge identity
layer is the one place where a request's ``Authorization: Bearer <key>``
becomes *both* a hermes :class:`UserAccount` *and* the CRM's
``{role_id, department_id, is_admin}`` triple the
:class:`hermes.crm.permission.Permission` checker expects.

Before this module, the two halves spoke past each other:

* hermes routes resolved tokens via ``request.app.state.auth_store`` and
  saw user ids as opaque decimal strings.
* CRM routes (``hermes_cloud/app/crm/routes/``) expected a
  ``current_user`` with ``.id: int`` / ``.role_id: int`` and went
  straight to :class:`UsersDb`.

With the CRM now sharing the same ``users`` table (Phase 1c), we can
collapse the two paths. :func:`current_user` resolves a Bearer token
once and :func:`crm_context` fans out to
``users LEFT JOIN roles LEFT JOIN departments`` so the permission
checker has everything it needs without re-issuing queries per route.

Three public helpers:

* :func:`current_user` — FastAPI dependency. Resolves the Bearer
  token, returns a :class:`UserAccount` (hermes surface) or raises
  401.
* :func:`crm_context` — decorates a user id with CRM fields. Returns
  a :class:`CrmContext` or ``None`` when the user doesn't exist / CRM
  DB is unreachable. Intended to sit behind :func:`current_user` in
  the dep chain for ``/crm/*`` routes.
* :func:`ensure_crm_identity` — idempotent default-role assignment
  for newly-registered users. Called from the registration path in
  ``create_app`` / route handlers so a freshly registered hermes user
  can hit ``/crm/*`` without a manual admin grant.

All three degrade gracefully when the CRM DB is unreachable (returns
``None`` / no-op), matching the scheme-gated boot pattern set up in
:mod:`hermes_cloud.app.main`. That keeps unit-test suites that use
``AppConfig()`` (no session store URL, InMemory auth) from blowing up
on import.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request, status

from hermes_cloud.app.auth import require_bearer_auth
from hermes_cloud.app.auth_store import UserAccount
from hermes_cloud.app.config import AppConfig


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CrmContext
# ---------------------------------------------------------------------------

# Name of the lowest-privilege seed role (see ``seed.DEFAULT_ROLES``).
# Kept in sync with that seed list; if the name ever changes, update here
# too — the test harness enforces this invariant.
DEFAULT_CRM_ROLE_NAME = "Sales"


@dataclass
class CrmContext:
    """Everything a CRM route needs about the calling user in one object.

    Built by joining ``users`` with ``roles`` and ``departments``. The
    integer fields (``user_id`` / ``role_id`` / ``department_id``)
    match the CRM's native ``BIGINT`` types — callers can pass them
    straight into :class:`Permission` without re-casting.

    ``team_ids`` is reserved for Phase 1e (team-scoped queries). For
    now it contains just ``[department_id]`` if the user has one, so
    callers can already read "the teams this user belongs to" without
    having to change the call site later. Hierarchy expansion (via
    :meth:`DepartmentsDb.get_department_and_children_ids`) is a
    follow-up — uncommon in Phase 1 routes, and the join cost adds up
    on the permission hot-path.
    """

    user_id: int
    is_admin: bool
    role_id: Optional[int]
    role_name: str
    department_id: Optional[int]
    department_name: str
    team_ids: list[int]


# ---------------------------------------------------------------------------
# current_user
# ---------------------------------------------------------------------------


def current_user(request: Request) -> UserAccount:
    """Resolve the Bearer token on *request* into a :class:`UserAccount`.

    Differences from :func:`hermes_cloud.app.auth.require_bearer_auth`:

    * Always requires auth — no "auth not configured → allow" escape
      hatch. CRM routes are never publicly accessible even in dev
      mode, so we reject unauthenticated callers outright (matches
      kacrm's ``require_login`` semantics).
    * Returns the full :class:`UserAccount` so downstream deps (like
      :func:`crm_context`) don't have to re-fetch by id.

    Raises ``HTTPException(401)`` if the token is missing or unknown,
    or if the underlying :class:`AuthStore` doesn't support ``get_user``
    (e.g. a bare :class:`InMemoryAuthStore` without a registered
    account). The dashboard's cookie path is *not* consulted — the
    bridge only understands Bearer tokens. Cookie-bearing HTML routes
    still go through ``require_dashboard_auth``.
    """
    config: AppConfig = getattr(
        request.app.state, "config", None
    ) or AppConfig()
    user_id = require_bearer_auth(request, config)
    if not user_id:
        # ``require_bearer_auth`` only returns ``None`` when auth is
        # unconfigured. CRM is never public, so escalate to 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Missing or invalid bearer token.",
                    "type": "authentication_error",
                    "code": "unauthorized",
                }
            },
        )
    auth_store = getattr(request.app.state, "auth_store", None)
    user: Optional[UserAccount] = None
    if auth_store is not None and hasattr(auth_store, "get_user"):
        user = auth_store.get_user(user_id)
    if user is None:
        # Token resolved (so it's either a seeded static key or a
        # row that's since been soft-deleted) but we can't hydrate
        # the full account. Treat as invalid — better than silently
        # letting through a half-identity.
        logger.warning("current_user_hydrate_failed uid=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "User account is unavailable.",
                    "type": "authentication_error",
                    "code": "unauthorized",
                }
            },
        )
    return user


# ---------------------------------------------------------------------------
# crm_context
# ---------------------------------------------------------------------------


def _coerce_uid(user_id: str) -> Optional[int]:
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _crm_ctx_row(uid: int) -> Optional[dict[str, Any]]:
    """Return the joined ``users LEFT JOIN roles LEFT JOIN departments`` row.

    Swallows ImportError / connection errors so non-CRM unit-test
    suites (in-memory auth store, no PG) don't fail on import of this
    module. In those configurations CRM routes aren't reachable
    anyway, so the ``None`` return is the correct answer.
    """
    try:
        from hermes.crm.db import db_session
    except Exception:  # pragma: no cover — defensive
        return None
    try:
        with db_session() as db:
            return db.fetchone(
                """
                SELECT
                    u.id          AS user_id,
                    u.is_admin    AS is_admin,
                    u.role_id     AS role_id,
                    r.name        AS role_name,
                    u.department_id AS department_id,
                    d.name        AS department_name
                FROM users u
                LEFT JOIN roles r       ON u.role_id = r.id
                LEFT JOIN departments d ON u.department_id = d.id
                WHERE u.id = %s AND u.deleted = FALSE AND u.status = 1
                LIMIT 1
                """,
                (uid,),
            )
    except Exception as exc:  # pragma: no cover — exercised in tests
        logger.warning("crm_context_query_failed uid=%s err=%r", uid, exc)
        return None


def crm_context(user_id: str) -> Optional[CrmContext]:
    """Build the CRM context (``role_id``, ``department_id`` …) for *user_id*.

    ``user_id`` follows the protocol-level decimal-string convention
    (``"1"``, ``"17"``). Non-integer ids, soft-deleted / disabled users,
    and unreachable DBs all return ``None`` — callers should 403 when
    that happens rather than falling back to a permissive default.
    """
    uid = _coerce_uid(user_id)
    if uid is None:
        return None
    row = _crm_ctx_row(uid)
    if row is None:
        return None
    did = row.get("department_id")
    team_ids: list[int] = [int(did)] if did is not None else []
    return CrmContext(
        user_id=int(row["user_id"]),
        is_admin=bool(row.get("is_admin")),
        role_id=int(row["role_id"]) if row.get("role_id") is not None else None,
        role_name=(row.get("role_name") or ""),
        department_id=int(did) if did is not None else None,
        department_name=(row.get("department_name") or ""),
        team_ids=team_ids,
    )


# ---------------------------------------------------------------------------
# ensure_crm_identity
# ---------------------------------------------------------------------------


def _default_role_id() -> Optional[int]:
    """Look up the seeded default role id (``Sales`` per ``DEFAULT_ROLES``).

    Returns ``None`` if the CRM DB is unreachable OR the seed never
    ran (fresh test DB). Callers must cope with that — we don't want
    to auto-seed roles from this path; seed ordering is the seed
    script's responsibility.
    """
    try:
        from hermes.crm.db import db_session
    except Exception:  # pragma: no cover — defensive
        return None
    try:
        with db_session() as db:
            row = db.fetchone(
                "SELECT id FROM roles WHERE name = %s LIMIT 1",
                (DEFAULT_CRM_ROLE_NAME,),
            )
            if row and row.get("id") is not None:
                return int(row["id"])
            # Fallback: any ``is_default = TRUE`` row, lowest id.
            row = db.fetchone(
                "SELECT id FROM roles WHERE is_default = TRUE "
                "ORDER BY id ASC LIMIT 1"
            )
            return int(row["id"]) if row and row.get("id") is not None else None
    except Exception as exc:  # pragma: no cover — exercised in tests
        logger.warning("default_role_lookup_failed err=%r", exc)
        return None


def ensure_crm_identity(user_id: str) -> Optional[int]:
    """Assign the default CRM role to *user_id* if they don't have one yet.

    Idempotent: if the user already has ``role_id`` set (e.g. via
    kacrm seed data or an admin grant), we leave it alone. Returns
    the role id that's now on the user row, or ``None`` when we
    couldn't touch the DB (unreachable / bad user id / no seed role).

    Intended to be called:

    1. At registration time, right after :meth:`AuthStore.register_user`
       returns a fresh ``UserAccount`` — this is what lets a brand-new
       hermes user hit the CRM routes without an admin doing a role
       assignment.
    2. On first CRM access as a safety net — so pre-Phase-1c users
       that registered before this code existed get backfilled on
       their first CRM request instead of 403'ing in perpetuity.
    """
    uid = _coerce_uid(user_id)
    if uid is None:
        return None
    try:
        from hermes.crm.db import db_session
    except Exception:  # pragma: no cover — defensive
        return None
    try:
        with db_session() as db:
            current = db.fetchone(
                "SELECT role_id FROM users WHERE id = %s AND deleted = FALSE",
                (uid,),
            )
            if current is None:
                return None
            if current.get("role_id") is not None:
                return int(current["role_id"])
            role_id = _default_role_id()
            if role_id is None:
                # No seeded role to assign. Silent no-op — the caller
                # can retry later once the seed has run.
                return None
            db.execute(
                "UPDATE users SET role_id = %s WHERE id = %s",
                (role_id, uid),
            )
            return role_id
    except Exception as exc:  # pragma: no cover — exercised in tests
        logger.warning("ensure_crm_identity_failed uid=%s err=%r", user_id, exc)
        return None


def by_uuid(user_id: str) -> Optional[CrmContext]:
    """Resolve a user by their string id and return CRM context.

    Convenience alias for :func:`crm_context` — named ``by_uuid`` to
    match the plan §6.1 spec (originally intended for UUID-typed ids,
    but the schema switched to BIGSERIAL; the name is kept for backward
    compatibility with call-sites that already reference it).
    """
    return crm_context(user_id)


__all__ = [
    "CrmContext",
    "DEFAULT_CRM_ROLE_NAME",
    "current_user",
    "crm_context",
    "ensure_crm_identity",
    "by_uuid",
]
