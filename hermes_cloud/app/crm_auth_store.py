"""``AuthStore`` backed by the CRM ``users`` table.

Phase 1c of the hermes × CRM merge (see
``docs/2026-04-15-hermes-crm-implementation-plan.md`` §4.1 + "决策 D1–D5"):
all hermes-cloud identity goes through the same PG ``users`` table the
CRM subsystem already uses, so one login unlocks both the Agent
dashboard and the ``/crm/*`` endpoints.

This module is the drop-in adaptor from the historic ``AuthStore``
protocol (``authenticate_user`` / ``register_user`` / ``get_user`` /
``has_user_keys`` / ...) onto the CRM schema.

What this store does NOT do (and why):

* **Token hashing** — the original ``SqlAlchemyAuthStore`` stored a
  SHA-256 of each bearer token and compared hashes on lookup. The CRM
  schema keeps the raw ``api_key`` column (``users.api_key`` is indexed
  and unique). Per the implementation plan §4.1 the canonical key is
  rotated on breach rather than hashed-at-rest, so we follow the CRM
  convention. If multi-token / hashed-at-rest support is needed later
  it lives in a separate ``hermes_user_api_keys`` table (see §4.1
  snippet).

* **Separate ``admins`` table** — the CRM schema promotes admin to a
  boolean flag on ``users`` itself (``users.is_admin``). No stub
  ``admins`` / ``admin_api_keys`` rows get created; admin auth
  round-trips through the same ``api_key`` column with an ``is_admin``
  check.

* **Email-keyed login** — CRM uses ``username`` as the primary login
  identifier and ``email`` as a secondary. We accept either in
  :meth:`authenticate_with_password`, per decision D4 in the plan.

Protocol mapping:

    AuthStore method                 → CRM ``users`` column / query
    ───────────────────────────────────────────────────────────────────
    authenticate_user(token)         → WHERE api_key = token
    get_user(user_id)                → WHERE id = int(user_id)
    has_user_keys()                  → SELECT 1 WHERE api_key IS NOT NULL
    list_user_ids()                  → SELECT id WHERE api_key IS NOT NULL
    register_user(email, password)   → INSERT users(...) + api_key
    authenticate_with_password(...)  → SELECT ... WHERE email|username =
                                       ? + bcrypt check
    seed_user_key(token, user_id)    → UPDATE users SET api_key = token
    seed_admin_key(token, admin_id)  → seed + SET is_admin = TRUE

User ids are returned as **decimal-string integers** (``"1"``, ``"17"``)
to match the ``AuthStore`` protocol's ``str`` contract. Callers are
expected to cast back to ``int`` at the boundary with the CRM layer.
"""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hermes_cloud.app.auth_store import UserAccount
from hermes.crm.db import DbWrapper, db_session
from hermes.crm.models.users_db import UsersDb


logger = logging.getLogger(__name__)


def _generate_api_key() -> str:
    """48-char hex token — same entropy as the historical hermes store."""
    return secrets.token_hex(24)


def _row_to_account(row: Dict[str, Any]) -> UserAccount:
    """Map a ``users`` row dict into the legacy :class:`UserAccount` shape.

    Intentional column remapping:
      * ``users.password`` → ``UserAccount.password_hash`` (it IS a bcrypt
        hash despite the column name — see kacrm naming retained per D1).
      * ``users.status == 1`` → ``is_active`` (CRM uses SMALLINT status;
        ``0`` means disabled, ``1`` active).
      * ``users.id`` (BIGINT) → ``UserAccount.id`` (decimal str).
    """
    created_at = row.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = datetime.now(timezone.utc)
    return UserAccount(
        id=str(int(row["id"])),
        email=(row.get("email") or "").strip(),
        password_hash=(row.get("password") or ""),
        is_active=bool(int(row.get("status") or 0) == 1 and not row.get("deleted")),
        created_at=created_at,
        api_key=(row.get("api_key") or ""),
        plan=(row.get("plan") or "free"),
        is_admin=bool(row.get("is_admin")),
    )


@dataclass
class _UsernameDerivation:
    """Captured so tests can replace it without patching module state."""

    fn: Any

    def __call__(self, email: str) -> str:
        return self.fn(email)


def _default_username_from_email(email: str) -> str:
    """Derive a slug-safe ``users.username`` from a signup email.

    Charter v2 §简化原则: ``profile.id == users.username`` and is used
    as a filesystem path / URL component. The DB constraint
    ``ck_users_username_slug`` enforces ``^[a-z0-9][a-z0-9_-]{0,49}$``
    on every row.

    Derivation rules to satisfy that regex:
      1. Take the email local-part, lowercase
      2. Replace any non-[a-z0-9_-] character (including ``.``, which
         email allows but the slug regex doesn't) with ``-``
      3. Collapse runs of ``-`` and trim leading/trailing ``-``
      4. Drop leading non-alphanumeric (regex requires the first char
         be ``[a-z0-9]``)
      5. Empty or all-stripped → fallback ``"user"``
      6. Truncate to 40 chars to leave headroom for collision suffix
         (the regex caps at 50)
    """
    local = (email or "").split("@", 1)[0].strip().lower()
    # Replace every char that's not in the ASCII slug alphabet with '-'.
    # ``str.isalnum`` treats CJK / accented chars as alphanumeric per
    # Unicode, but the slug regex is strict ASCII; force-check both.
    def _ok(c: str) -> bool:
        return (c.isascii() and (c.isalnum() or c in "-_"))
    cleaned = "".join(c if _ok(c) else "-" for c in local)
    # Collapse repeated dashes.
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-_")
    # Regex requires first char ∈ [a-z0-9] (ASCII alphanum); drop
    # leading non-conforming chars.
    while cleaned and not (cleaned[0].isascii() and cleaned[0].isalnum()):
        cleaned = cleaned[1:]
    if not cleaned:
        cleaned = "user"
    return cleaned[:40]


# Match the DB constraint ``ck_users_username_slug``. Application-layer
# validation gives friendlier errors than waiting for the constraint to
# fire on INSERT.
_USERNAME_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")


def _validate_username_slug(username: str) -> str:
    """Raise ValueError with a clear message if *username* doesn't match
    the slug shape Charter v2 / DB requires."""
    if not _USERNAME_SLUG_RE.match(username or ""):
        raise ValueError(
            f"username {username!r} must match {_USERNAME_SLUG_RE.pattern!r} "
            f"(lowercase letters/digits/hyphens/underscores, 1-50 chars, "
            f"first char must be alphanumeric)"
        )
    return username


class CrmUsersAuthStore:
    """``AuthStore`` implementation routed through CRM's ``users`` table.

    Thread-safe through the connection pool (``db_session`` borrows a
    pooled connection per call). No in-process caching — every lookup
    round-trips to the DB, keeping multi-worker deployments consistent.
    """

    # Keep a method-level hook so future callers (e.g. an admin CLI
    # provisioning demo users) can inject their own username policy.
    _derive_username = staticmethod(_default_username_from_email)

    # ------------------------------------------------------------------
    # Bearer-token auth
    # ------------------------------------------------------------------

    def authenticate_user(self, token: str) -> Optional[str]:
        if not token:
            return None
        with db_session() as db:
            row = UsersDb(db).get_user_by_api_key(token)
        if not row:
            return None
        return str(int(row["id"]))

    def authenticate_admin(self, token: str) -> Optional[str]:
        user_id = self.authenticate_user(token)
        if user_id is None:
            return None
        acct = self.get_user(user_id)
        if acct is None or not acct.is_admin:
            return None
        return user_id

    def has_user_keys(self) -> bool:
        with db_session() as db:
            got = db.fetchcolumn(
                "SELECT 1 FROM users "
                "WHERE api_key IS NOT NULL AND api_key <> '' "
                "AND deleted = FALSE AND status = 1 LIMIT 1"
            )
        return got is not None

    def has_admin_keys(self) -> bool:
        with db_session() as db:
            got = db.fetchcolumn(
                "SELECT 1 FROM users "
                "WHERE is_admin = TRUE "
                "AND api_key IS NOT NULL AND api_key <> '' "
                "AND deleted = FALSE AND status = 1 LIMIT 1"
            )
        return got is not None

    def list_user_ids(self) -> List[str]:
        with db_session() as db:
            rows = db.fetchall(
                "SELECT id FROM users "
                "WHERE api_key IS NOT NULL AND api_key <> '' "
                "AND deleted = FALSE AND status = 1 "
                "ORDER BY id ASC"
            )
        return [str(int(r["id"])) for r in rows]

    # ------------------------------------------------------------------
    # Password-based accounts
    # ------------------------------------------------------------------

    def register_user(
        self, email: str, password: str, *, is_admin: bool = False
    ) -> UserAccount:
        email_norm = (email or "").strip().lower()
        if not email_norm:
            raise ValueError("email is required")
        if not password:
            raise ValueError("password is required")

        api_key = _generate_api_key()
        password_hash = UsersDb.hash_password(password)
        username_base = self._derive_username(email_norm)
        # Validate the slug shape upfront — friendlier than letting the
        # DB CHECK constraint fire mid-INSERT.
        _validate_username_slug(username_base)

        # A single retry covers the rare collision of auto-derived
        # usernames; after that we give up with a clear error so the
        # caller can surface "pick a different email/username".
        for attempt in range(2):
            username = (
                username_base
                if attempt == 0
                else f"{username_base}-{secrets.token_hex(2)}"
            )
            try:
                with db_session() as db:
                    users = UsersDb(db)
                    if users.email_exists(email_norm):
                        raise ValueError(
                            f"Email already registered: {email_norm}"
                        )
                    if users.username_exists(username):
                        if attempt == 0:
                            continue
                        raise ValueError(
                            f"Username already taken: {username}"
                        )
                    new_id = users.insert(
                        {
                            "username": username,
                            "password": password_hash,
                            "email": email_norm,
                            "api_key": api_key,
                            "plan": "free",
                            "is_admin": bool(is_admin),
                            "status": 1,
                        }
                    )
                    row = users.get_user_by_id(new_id)
                break
            except ValueError:
                raise
            except Exception as e:  # pragma: no cover — surfaced to caller
                logger.exception("register_user failed: %s", e)
                raise
        else:  # pragma: no cover — loop falls through only on repeated collision
            raise ValueError("username collision — retry with a different email")

        if not row:
            raise RuntimeError("register_user: INSERT did not return a row")
        return _row_to_account(row)

    def authenticate_with_password(
        self, email: str, password: str
    ) -> Optional[UserAccount]:
        """Accept either an email or a username as the identifier.

        Plan decision D4 ("登录支持 email 还是 username？ 两者都支持") —
        we do a cheap OR lookup, then bcrypt-check whatever row matches.
        """
        identifier = (email or "").strip()
        if not identifier or not password:
            return None
        # lowercase only when treating as email; usernames stay
        # case-sensitive per kacrm convention.
        email_norm = identifier.lower()
        with db_session() as db:
            row = db.fetchone(
                "SELECT * FROM users "
                "WHERE (LOWER(email) = %s OR username = %s) "
                "  AND deleted = FALSE AND status = 1 "
                "LIMIT 1",
                (email_norm, identifier),
            )
            if not row:
                return None
            users = UsersDb(db)
            if not users.verify_password(int(row["id"]), password):
                return None
            # Re-fetch after verify_password so any normalisation (e.g.
            # ``$2y$`` → ``$2b$`` on the stored hash) is reflected.
            row = users.get_user_by_id(int(row["id"]))
        if not row:
            return None
        return _row_to_account(row)

    def list_users(self) -> List[UserAccount]:
        with db_session() as db:
            rows = db.fetchall(
                "SELECT * FROM users WHERE deleted = FALSE "
                "ORDER BY created_at ASC, id ASC"
            )
        return [_row_to_account(r) for r in rows]

    def get_user(self, user_id: str) -> Optional[UserAccount]:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        with db_session() as db:
            row = UsersDb(db).get_user_by_id(uid)
        if not row or row.get("deleted"):
            return None
        return _row_to_account(row)

    def update_user(
        self,
        user_id: str,
        *,
        is_active: Optional[bool] = None,
        plan: Optional[str] = None,
        is_admin: Optional[bool] = None,
    ) -> Optional[UserAccount]:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        sets: List[str] = []
        params: List[Any] = []
        if is_active is not None:
            sets.append("status = %s")
            params.append(1 if is_active else 0)
        if plan is not None:
            sets.append("plan = %s")
            params.append(plan)
        if is_admin is not None:
            sets.append("is_admin = %s")
            params.append(bool(is_admin))
        if not sets:
            return self.get_user(user_id)
        params.append(uid)
        with db_session() as db:
            db.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = %s",
                tuple(params),
            )
            row = UsersDb(db).get_user_by_id(uid)
        if not row:
            return None
        return _row_to_account(row)

    def delete_user(self, user_id: str) -> bool:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False
        with db_session() as db:
            row = UsersDb(db).get_user_by_id(uid)
            if not row:
                return False
            UsersDb(db).soft_delete(uid)
        return True

    def reset_password(self, user_id: str, new_password: str) -> bool:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False
        if not new_password:
            return False
        new_hash = UsersDb.hash_password(new_password)
        with db_session() as db:
            return UsersDb(db).update_password(uid, new_hash)

    def create_user_by_admin(
        self,
        email: str,
        password: str,
        plan: str = "free",
        *,
        is_admin: bool = False,
    ) -> UserAccount:
        account = self.register_user(email, password, is_admin=is_admin)
        if plan != "free":
            updated = self.update_user(account.id, plan=plan)
            if updated is not None:
                return updated
        return account

    # ------------------------------------------------------------------
    # Token provisioning (kept for protocol compat — real CRM workflows
    # mint keys at registration via ``register_user``).
    # ------------------------------------------------------------------

    def seed_user_key(self, *, token: str, user_id: str) -> None:
        """Write ``api_key = token`` into ``users.id = user_id``.

        Used by the boot path to pin a static Bearer token to an existing
        CRM user (test fixtures, CI deploys). If the user doesn't exist
        we log and return — unlike the legacy store we don't auto-create
        a stub row, because ``users`` has NOT-NULL columns (``username``,
        ``password``) that can't be fabricated safely.
        """
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            logger.warning("seed_user_key: non-integer user_id=%r", user_id)
            return
        if not token:
            return
        with db_session() as db:
            row = UsersDb(db).get_user_by_id(uid)
            if not row:
                logger.warning(
                    "seed_user_key: no user with id=%s — skipping", uid
                )
                return
            db.execute(
                "UPDATE users SET api_key = %s WHERE id = %s", (token, uid)
            )

    def seed_admin_key(self, *, token: str, admin_id: str) -> None:
        try:
            uid = int(admin_id)
        except (TypeError, ValueError):
            logger.warning("seed_admin_key: non-integer admin_id=%r", admin_id)
            return
        if not token:
            return
        with db_session() as db:
            row = UsersDb(db).get_user_by_id(uid)
            if not row:
                logger.warning(
                    "seed_admin_key: no user with id=%s — skipping", uid
                )
                return
            db.execute(
                "UPDATE users SET api_key = %s, is_admin = TRUE WHERE id = %s",
                (token, uid),
            )


__all__ = ["CrmUsersAuthStore"]
