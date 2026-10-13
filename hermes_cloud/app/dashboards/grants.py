"""Grant model for dashboards (spec §5.c).

Three grant tiers, one per axis of permission risk:

- ``see_revenue``                    — money-shaped measures (e.g. accounts.deal_amount)
- ``see_team_member_performance``   — per-person breakdowns of acceptance/recall etc.
- ``see_org_overview``              — required for ``scope=org`` queries
- ``see_hr_signals``                — placeholder for future HR/turnover measures

Each user gets a base set from their role (``ROLE_DEFAULT_GRANTS``) plus any
explicit additions stored in ``user_grants`` (migration 0056).

The role parameter is a string ("employee" / "manager" / "admin") here; the
caller (typically routes._user_context) is responsible for resolving the
actual users.role_id / users.is_admin into one of these buckets per RECON
note R-4 in the plan. We do not query ``roles`` directly because role naming
is project-specific and changes; the dashboards module stays decoupled.
"""
from __future__ import annotations

from typing import Optional, Set


GRANT_NAMES: Set[str] = {
    "see_revenue",
    "see_team_member_performance",
    "see_org_overview",
    "see_hr_signals",
}

ROLE_DEFAULT_GRANTS: dict[str, Set[str]] = {
    "employee": set(),
    "manager": {"see_revenue", "see_team_member_performance"},
    "admin": {
        "see_revenue",
        "see_team_member_performance",
        "see_org_overview",
        "see_hr_signals",
    },
}


def grants_for_user(db, user_id: int, role: str) -> Set[str]:
    """Return effective grant set: role defaults ∪ explicit user_grants rows.

    ``db`` is a ``hermes.crm.db.DbWrapper``; pass None to skip the DB lookup
    (useful in tests / caller already knows there are no explicit grants).
    """
    role_grants = ROLE_DEFAULT_GRANTS.get(role, set())
    if db is None:
        return set(role_grants)
    rows = db.fetchall(
        "SELECT grant_name FROM user_grants WHERE user_id = %s",
        (user_id,),
    )
    explicit = {r["grant_name"] for r in rows}
    return set(role_grants) | explicit


def has_grant(db, *, user_id: int, role: str, grant: str) -> bool:
    """Convenience predicate. Raises ValueError on unknown grant name."""
    if grant not in GRANT_NAMES:
        raise ValueError(f"unknown grant: {grant!r}")
    return grant in grants_for_user(db, user_id, role)
