"""Process identity helper — Charter v2 §简化原则.

Single source of truth for "which user does this hermes turn belong to":
the profile ``HERMES_HOME``. The directory's basename IS the user_id
(slug-form).

Per Charter v2:
- 1 user = 1 profile
- profile_id == user_id (same value, same string)
- HERMES_HOME = /Users/.../profiles/{user_id}/

Multiplex note (kacrm single-process multiplexer)
-------------------------------------------------
The original Charter v2 assumption "1 user = 1 process" no longer holds:
kacrm runs ONE gateway process that serves MANY profiles, resolving the
active profile **per turn** via a context-local ``HERMES_HOME`` override
(``hermes_constants.get_hermes_home()`` honors it, and it propagates into
the agent's worker thread via ``copy_context()``). So identity MUST be
resolved per call, never frozen at process start — a frozen identity would
record tenant A's writes under tenant B (cross-tenant data leak).

Two safety properties therefore replace the old ``@lru_cache(maxsize=1)``:

- **Per-turn resolution** via ``get_hermes_home()`` (honors the override).
- **Fail-closed** (mirrors ``agent.secret_scope.get_secret``): when
  multiplexing is active but no per-turn profile scope is installed, we
  RAISE ``UnscopedIdentityError`` rather than silently resolving the
  process default. A missed scope crashes loudly at that line instead of
  attributing the turn to the wrong tenant.

When multiplexing is OFF (the single-profile default) behavior is
byte-for-byte identical to the legacy ``os.environ["HERMES_HOME"]`` read.
"""
from __future__ import annotations

import os
from pathlib import Path

# hermes_constants / agent.secret_scope resolve as top-level modules inside
# the gateway subprocess (PYTHONPATH=hermes) and as ``hermes.*`` when hermes
# is installed as a package in the cloud process. Try both so identity works
# in either import context without a hard directional dependency on the
# package layout.
try:  # pragma: no cover - import-shim, exercised in both deployments
    from hermes_constants import get_hermes_home, get_hermes_home_override
except ImportError:  # pragma: no cover
    from hermes.hermes_constants import get_hermes_home, get_hermes_home_override

try:  # pragma: no cover - import-shim
    from agent.secret_scope import is_multiplex_active
except ImportError:  # pragma: no cover
    from hermes.agent.secret_scope import is_multiplex_active


class UnscopedIdentityError(RuntimeError):
    """Raised when identity is resolved under multiplexing with no per-turn scope.

    Fail-closed signal (mirrors ``agent.secret_scope.UnscopedSecretError``):
    the process is a profile multiplexer, but ``current_user_id()`` was called
    with no context-local ``HERMES_HOME`` override installed. Resolving the
    process default here would attribute this turn to the wrong tenant, so we
    raise instead. The fix is to run the call inside the per-turn profile scope
    (``hermes_constants.set_hermes_home_override(...)`` /
    ``gateway.run._profile_runtime_scope``), never to relax this check.
    """


def current_user_id() -> str:
    """The user identity (slug, string) this hermes TURN is bound to.

    Resolved per call from the active profile ``HERMES_HOME`` basename (which
    equals ``users.username`` — Charter v2 §简化原则). Under multiplexing this
    reads the context-local per-turn override; single-profile deployments read
    the process ``HERMES_HOME`` exactly as before.

    Raises:
        UnscopedIdentityError: multiplexing is active but no per-turn profile
            scope is installed (fail-closed — never resolve the default).
        RuntimeError: no override and ``HERMES_HOME`` unset (single-profile
            loud failure, unchanged from the legacy behavior), or the resolved
            home has an empty basename.
    """
    override = get_hermes_home_override()

    # Fail-closed: a multiplexer turn MUST carry a per-turn profile scope.
    # Never fall back to the process default — that would leak tenant identity.
    if is_multiplex_active() and override is None:
        raise UnscopedIdentityError(
            "current_user_id() called with no per-turn profile scope while "
            "multiplexing is on. Identity must be resolved inside a "
            "set_hermes_home_override(...) block (the per-turn profile scope); "
            "resolving the process default here would record this turn under "
            "the wrong tenant. See hermes_cloud/app/identity.py."
        )

    # Single-profile (no override): keep the legacy loud failure when
    # HERMES_HOME is unset, rather than letting get_hermes_home() silently
    # fall back to the platform default (~/.hermes) under a bogus identity.
    if override is None and not os.environ.get("HERMES_HOME", "").strip():
        raise RuntimeError(
            "HERMES_HOME env not set. A non-multiplex hermes process must be "
            "bound to one user via HERMES_HOME."
        )

    # get_hermes_home() honors the per-turn override first, then HERMES_HOME.
    name = Path(get_hermes_home()).name
    if not name:
        raise RuntimeError(
            "Resolved HERMES_HOME has an empty basename; cannot resolve user_id."
        )
    return name


# Per-slug bigint cache. Keyed by profile slug so a multiplexer serving many
# tenants never collapses them the way a process-wide ``@lru_cache(maxsize=1)``
# would (that cache returned tenant A's id for tenant B's slug). Slugs are
# immutable, so caching the slug→id mapping for the process lifetime is safe.
_uid_cache: dict[str, int] = {}


def current_user_id_int() -> int:
    """Legacy bridge — return bigint user.id for CRM tables that still use bigint.

    Charter v2 Phase 4 will migrate the CRM tables (leads.created_by /
    accounts.created_by / memories.user_id) from bigint to varchar; until
    then, callers writing into those columns need an int.

    Resolves via ``users.username`` (which equals ``current_user_id()``).
    Inherits the per-turn resolution + fail-closed guard from
    ``current_user_id()``. The slug→id lookup is cached per slug (see
    ``_uid_cache``) so it stays correct under multiplexing.
    """
    slug = current_user_id()  # inherits fail-closed + per-turn resolution
    cached = _uid_cache.get(slug)
    if cached is not None:
        return cached

    # Lazy import — db is heavy. ``hermes.crm.db`` in the cloud process,
    # top-level ``crm.db`` in the gateway subprocess.
    try:
        from hermes.crm.db import db_session
    except ImportError:
        from crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = %s AND deleted = FALSE LIMIT 1",
            (slug,),
        )
    if row is None:
        raise RuntimeError(
            f"No active user with username={slug!r}. "
            f"HERMES_HOME basename must match a users.username row."
        )
    uid = int(row["id"])
    _uid_cache[slug] = uid
    return uid


__all__ = ["current_user_id", "current_user_id_int", "UnscopedIdentityError"]
