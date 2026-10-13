"""``ProfileStore`` backed by the shared CRM PostgreSQL database.

Phase 1c.3a of the hermes × CRM merge: every identity-adjacent table
(``users``, ``profiles``, ``changelog``, ``interactions``, ...) lives
in one PG and shares one connection pool. This module is the
psycopg3-backed implementation of the ``ProfileStore`` protocol, sitting
next to :class:`CrmUsersAuthStore` and following the same conventions
(string user-ids at the protocol surface, cast to ``BIGINT`` at the DB
boundary).

Schema notes (Charter v2 §简化原则, alembic head 0109):

* **1 user = 1 profile.** ``profiles.id`` is the user's
  ``users.username`` (VARCHAR slug); there is no separate hex id and
  no multi-profile-per-user feature. :meth:`create_profile` is kept
  only as a hard-fail guard — see its docstring.

* There is **no ``user_id`` column** on ``profiles``. The link to a
  user is the relation ``profiles.id == users.username``; every read
  resolves the bigint ``users.id`` via :data:`_PROFILES_SELECT`'s
  ``JOIN users u ON u.username = p.id``. The protocol's ``str``
  user-id is the decimal ``users.id``, cast to ``int`` on every call;
  if the cast fails we return ``None`` / ``[]`` / ``False`` rather
  than raising — defensive posture for garbage ids.

The store preserves the legacy-row reuse path in
:meth:`ensure_default_profile` (a pre-existing ``profiles.id ==
username`` row with ``is_default = FALSE`` gets flipped on rather than
duplicated) and soft-delete semantics for :meth:`delete_profile`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from hermes.crm.db import db_session
from hermes_cloud.app.profile_store import (
    CannotDeleteDefaultProfileError,
    DEFAULT_PROFILE_NAME,
    Profile,
    _slugify,
    _unique_slug,
    _validate_name,
    _validate_slug,
)


logger = logging.getLogger(__name__)


def _coerce_uid(user_id: str) -> Optional[int]:
    """Cast a protocol-level str user-id to the CRM's BIGINT.

    Returns ``None`` on garbage input so callers can degrade gracefully
    (matches the SA store's "None on bad id" posture under tests).
    """
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _row_to_profile(row: dict[str, Any]) -> Profile:
    """Build Profile from a row.

    Charter v2 §简化原则: profile.id == users.username (slug). The
    ``user_id`` field on the dataclass is preserved for API compat with
    callers that read ``profile.user_id`` as a bigint string — its
    value is sourced from a JOIN to ``users.id``, NOT from a
    ``profiles.user_id`` column (which v2 removed).

    The row must contain either:
      * ``user_id_int`` — bigint from JOIN ``users.id``  (preferred), or
      * ``user_id`` — legacy column (only present during transition)

    Raises KeyError if neither is present, so callers can't accidentally
    read a stale ``profile.user_id``.
    """
    created_at = row.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = datetime.now(timezone.utc)
    if "user_id_int" in row:
        uid = int(row["user_id_int"])
    elif "user_id" in row and row["user_id"] is not None:
        uid = int(row["user_id"])
    else:
        raise KeyError(
            "_row_to_profile: row missing user_id_int (JOIN result) or "
            "user_id (legacy column). Caller must SELECT one of them."
        )
    return Profile(
        id=str(row["id"]),
        user_id=str(uid),
        name=row.get("name") or "",
        is_default=bool(row.get("is_default")),
        is_active=bool(row.get("is_active")),
        created_at=created_at,
        description=row.get("description") or "",
        slug=row.get("slug") or "",
    )


# SQL fragment: every read of ``profiles`` MUST use this JOIN to also
# resolve the user_id (bigint user.id) needed by Profile.user_id.
_PROFILES_SELECT = (
    "SELECT p.*, u.id AS user_id_int "
    "FROM profiles p JOIN users u ON u.username = p.id"
)


class CrmProfileStore:
    """``ProfileStore`` implementation routed through the CRM PG pool.

    Thread-safe through the shared psycopg3 connection pool — every
    method borrows a connection for the duration of the call and
    returns it. No in-process caching (keeps multi-worker deploys
    consistent).
    """

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _taken_slugs(
        self, db, uid: int, *, exclude_id: Optional[str] = None
    ) -> set[str]:
        # v2 §简化原则: profiles has no user_id column; resolve user
        # via JOIN ``users.username = profiles.id``.
        sql = (
            "SELECT p.slug FROM profiles p "
            "JOIN users u ON u.username = p.id "
            "WHERE u.id = %s AND p.is_active = TRUE AND p.slug <> ''"
        )
        params: list[Any] = [uid]
        if exclude_id is not None:
            sql += " AND p.id <> %s"
            params.append(exclude_id)
        return {r["slug"] for r in db.fetchall(sql, tuple(params))}

    def _get_default_row(self, db, uid: int) -> Optional[dict[str, Any]]:
        return db.fetchone(
            f"{_PROFILES_SELECT} "
            "WHERE u.id = %s AND p.is_default = TRUE AND p.is_active = TRUE "
            "LIMIT 1",
            (uid,),
        )

    # ------------------------------------------------------------------
    # Protocol: ensure / create / get
    # ------------------------------------------------------------------

    def ensure_default_profile(self, *, user_id: str) -> Profile:
        uid = _coerce_uid(user_id)
        if uid is None:
            raise ValueError(f"user_id must be an integer string: {user_id!r}")
        with db_session() as db:
            # 1) already has an active default → return (backfill slug if empty).
            existing = self._get_default_row(db, uid)
            if existing is not None:
                if not existing.get("slug"):
                    new_slug = _unique_slug(
                        self._taken_slugs(db, uid, exclude_id=str(existing["id"])),
                        _slugify(existing.get("name") or DEFAULT_PROFILE_NAME),
                    )
                    db.execute(
                        "UPDATE profiles SET slug = %s WHERE id = %s",
                        (new_slug, str(existing["id"])),
                    )
                    existing["slug"] = new_slug
                return _row_to_profile(existing)

            # v2 §简化原则: profile.id is the user's username (slug),
            # NOT the bigint user.id stringified. Look up users.username
            # for the canonical identifier.
            user_row = db.fetchone(
                "SELECT username FROM users WHERE id=%s AND deleted=FALSE",
                (uid,),
            )
            if user_row is None:
                raise ValueError(
                    f"No active user with id={uid}; cannot create profile."
                )
            profile_id_slug = str(user_row["username"]).strip()
            if not profile_id_slug:
                raise ValueError(
                    f"User id={uid} has empty username; cannot use as profile.id."
                )

            # 2) legacy row with profile.id == username that's somehow not
            #    active/default → flip it on.
            legacy = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s LIMIT 1",
                (profile_id_slug,),
            )
            if legacy is not None:
                slug = legacy.get("slug") or _unique_slug(
                    self._taken_slugs(db, uid, exclude_id=profile_id_slug),
                    _slugify(legacy.get("name") or DEFAULT_PROFILE_NAME),
                )
                db.execute(
                    "UPDATE profiles "
                    "SET is_default = TRUE, is_active = TRUE, slug = %s "
                    "WHERE id = %s",
                    (slug, profile_id_slug),
                )
                row = db.fetchone(
                    f"{_PROFILES_SELECT} WHERE p.id = %s", (profile_id_slug,)
                )
                return _row_to_profile(row)

            # 3) fresh insert
            slug = _unique_slug(
                self._taken_slugs(db, uid), _slugify(DEFAULT_PROFILE_NAME)
            )
            # v2 §简化原则: the profiles table has NO user_id column — a
            # profile is bound to its user via id == users.username (see
            # _PROFILES_SELECT's JOIN). profile_id_slug IS that username, so the
            # association is implicit; writing a user_id column would reference a
            # column that doesn't exist (admin user-create 500'd here).
            db.execute(
                """
                INSERT INTO profiles
                    (id, name, slug, is_default, is_active, description)
                VALUES (%s, %s, %s, TRUE, TRUE, '')
                """,
                (profile_id_slug, DEFAULT_PROFILE_NAME, slug),
            )
            row = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s", (profile_id_slug,)
            )
            return _row_to_profile(row)

    def create_profile(
        self,
        *,
        user_id: str,
        name: str,
        description: str = "",
        slug: Optional[str] = None,
        max_profiles_per_user: Optional[int] = None,
    ) -> Profile:
        """**Removed** — Charter v2 §简化原则: 1 user = 1 profile.

        Multi-profile-per-user is gone: a user's sole profile is their
        default, with ``profiles.id == users.username`` (see
        :meth:`ensure_default_profile`). The ``POST /api/profiles``
        endpoint is a ``410 Gone``; this method is kept as a hard-fail
        (rather than deleted from the protocol) so any lingering call
        site surfaces immediately instead of silently writing an
        unroutable row. ``user_id`` has no column to land in and a hex
        ``id`` would JOIN to no user — both would be silent corruption.
        """
        raise NotImplementedError(
            "Charter v2 §简化原则: 1 user = 1 profile. Multi-profile-per-user "
            "is removed; each user has exactly one profile with "
            "profile_id == users.username. Use ensure_default_profile(). "
            "See docs/2026-05-05-hermes-crm-first-principles-v2.md."
        )

    def update_profile(
        self,
        profile_id: str,
        *,
        user_id: str,
        description: Optional[str] = None,
        slug: Optional[str] = None,
    ) -> Optional[Profile]:
        uid = _coerce_uid(user_id)
        if uid is None:
            return None
        with db_session() as db:
            row = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s LIMIT 1", (profile_id,)
            )
            if (
                row is None
                or int(row["user_id_int"]) != uid
                or not row.get("is_active")
            ):
                return None
            sets: list[str] = []
            params: list[Any] = []
            if slug is not None:
                new_slug = _validate_slug(slug)
                if new_slug != (row.get("slug") or ""):
                    taken = self._taken_slugs(db, uid, exclude_id=profile_id)
                    if new_slug in taken:
                        raise ValueError(
                            f"slug {new_slug!r} already in use for this user"
                        )
                    sets.append("slug = %s")
                    params.append(new_slug)
            if description is not None:
                sets.append("description = %s")
                params.append(description.strip())
            if sets:
                params.append(profile_id)
                db.execute(
                    f"UPDATE profiles SET {', '.join(sets)} WHERE id = %s",
                    tuple(params),
                )
                row = db.fetchone(
                    f"{_PROFILES_SELECT} WHERE p.id = %s", (profile_id,)
                )
            return _row_to_profile(row)

    def get_profile(self, profile_id: str) -> Optional[Profile]:
        with db_session() as db:
            row = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s LIMIT 1", (profile_id,)
            )
        return _row_to_profile(row) if row else None

    def get_default_profile(self, *, user_id: str) -> Optional[Profile]:
        uid = _coerce_uid(user_id)
        if uid is None:
            return None
        with db_session() as db:
            row = self._get_default_row(db, uid)
        return _row_to_profile(row) if row else None

    def list_profiles(
        self, *, user_id: str, include_inactive: bool = False
    ) -> List[Profile]:
        uid = _coerce_uid(user_id)
        if uid is None:
            return []
        sql = f"{_PROFILES_SELECT} WHERE u.id = %s"
        if not include_inactive:
            sql += " AND p.is_active = TRUE"
        sql += " ORDER BY p.is_default DESC, p.created_at ASC"
        with db_session() as db:
            rows = db.fetchall(sql, (uid,))
        return [_row_to_profile(r) for r in rows]

    def list_all_active_profiles(self) -> List[Profile]:
        """Cross-user enumeration for boot-time warm-up.

        Filters out profiles whose owning user is soft-deleted
        (``users.deleted=TRUE``). Without that filter, every cloud
        restart resurrects gateways for ghost users left over by
        test fixtures whose teardown only marked the user row deleted
        — creating ``~/.hermes/profiles/<id>/`` dirs and consuming
        ports for accounts no one can ever log into.
        """
        with db_session() as db:
            rows = db.fetchall(
                f"{_PROFILES_SELECT} "
                "WHERE p.is_active = TRUE AND u.deleted = FALSE "
                "ORDER BY u.id ASC, p.is_default DESC, p.created_at ASC"
            )
        return [_row_to_profile(r) for r in rows]

    def rename_profile(
        self, profile_id: str, *, user_id: str, new_name: str
    ) -> Optional[Profile]:
        uid = _coerce_uid(user_id)
        if uid is None:
            return None
        clean_name = _validate_name(new_name)
        with db_session() as db:
            row = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s LIMIT 1", (profile_id,)
            )
            if (
                row is None
                or int(row["user_id_int"]) != uid
                or not row.get("is_active")
            ):
                return None
            db.execute(
                "UPDATE profiles SET name = %s WHERE id = %s",
                (clean_name, profile_id),
            )
            row = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s", (profile_id,)
            )
        return _row_to_profile(row)

    def set_default(self, *, user_id: str, profile_id: str) -> bool:
        uid = _coerce_uid(user_id)
        if uid is None:
            return False
        with db_session() as db:
            target = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s LIMIT 1", (profile_id,)
            )
            if (
                target is None
                or int(target["user_id_int"]) != uid
                or not target.get("is_active")
            ):
                return False
            # v2: profiles has no user_id col; scope the toggle by joining
            # users via the username = profile.id relation.
            db.execute(
                """
                UPDATE profiles
                SET is_default = CASE WHEN id = %s THEN TRUE ELSE FALSE END
                WHERE id IN (
                    SELECT p.id FROM profiles p
                    JOIN users u ON u.username = p.id
                    WHERE u.id = %s
                )
                """,
                (profile_id, uid),
            )
        return True

    def delete_profile(self, profile_id: str, *, user_id: str) -> bool:
        uid = _coerce_uid(user_id)
        if uid is None:
            return False
        with db_session() as db:
            row = db.fetchone(
                f"{_PROFILES_SELECT} WHERE p.id = %s LIMIT 1", (profile_id,)
            )
            if row is None or int(row["user_id_int"]) != uid:
                return False
            if row.get("is_default"):
                raise CannotDeleteDefaultProfileError(
                    f"profile {profile_id!r} is the default for user {user_id!r}"
                )
            db.execute(
                "UPDATE profiles SET is_active = FALSE WHERE id = %s",
                (profile_id,),
            )
        return True


__all__ = ["CrmProfileStore"]
