"""Scope resolution for dashboard queries (spec §5.b).

Three scopes — self / team / org — each gated by:
  1. Role (employee=self only; manager=self|team; admin=self|team|org)
  2. Grant (org additionally requires ``see_org_overview``)

This module deliberately exposes no DB access. Callers pass in the
already-resolved role string (see grants.grants_for_user docstring on
the contract — admin via users.is_admin, manager via report_to_ids
reverse lookup, else employee). Concrete role resolution lives in the
routes layer where users/roles tables are accessible.
"""
from __future__ import annotations

from typing import Set


class ScopeError(Exception):
    """Raised when the caller requests a scope they cannot use."""


_DEFAULT_SCOPE: dict[str, str] = {
    "employee": "self",
    "manager": "team",
    "admin": "team",  # admin defaults to team, not org, per spec §5.b
}

_ALLOWED_SCOPES: dict[str, Set[str]] = {
    "employee": {"self"},
    "manager": {"self", "team"},
    "admin": {"self", "team", "org"},
}


def default_scope_for_role(role: str) -> str:
    """Default scope on a tab/chart for a given caller role."""
    return _DEFAULT_SCOPE.get(role, "self")


def allowed_scopes_for_role(role: str) -> Set[str]:
    """The full set of scopes a role MAY use (subject to grant gates)."""
    return set(_ALLOWED_SCOPES.get(role, {"self"}))


def resolve_scope(*, role: str, requested: str | None, grants: Set[str]) -> str:
    """Resolve final scope from (role, requested, grants).

    Policy:
      - ``requested=None`` -> ``default_scope_for_role(role)``
      - ``requested`` must be in ``allowed_scopes_for_role(role)``,
        else ``ScopeError``
      - ``scope='org'`` additionally requires ``see_org_overview`` grant
    """
    chosen = requested or default_scope_for_role(role)
    if chosen not in allowed_scopes_for_role(role):
        raise ScopeError(f"scope={chosen!r} not allowed for role={role!r}")
    if chosen == "org" and "see_org_overview" not in grants:
        raise ScopeError("scope='org' requires see_org_overview grant")
    return chosen
