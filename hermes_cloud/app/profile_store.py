"""ProfileStore — protocol + Profile dataclass + slug helpers.

Charter v2 §简化原则: 1 user = 1 profile. profile.id ==
``users.username`` (slug, validated by the ``ck_users_username_slug``
DB constraint to match ``^[a-z0-9][a-z0-9_-]{0,49}$``).

**No fallback implementations.** This module previously contained
``SqlAlchemyProfileStore`` and ``InMemoryProfileStore`` for dev/test
when PG was unavailable. v2 removes them — PG is the only supported
backend. The only concrete implementation lives in
:class:`hermes_cloud.app.crm_profile_store.CrmProfileStore`.

If you need an in-process fake for unit tests, write a MagicMock that
satisfies the :class:`ProfileStore` protocol — don't add a fallback
class here.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


DEFAULT_PROFILE_NAME = "main"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CannotDeleteDefaultProfileError(RuntimeError):
    """Raised when delete_profile() is called with the user's default."""


class ProfileQuotaExceededError(RuntimeError):
    """Raised when create_profile() would exceed the quota — kept for the
    error class identity only; v2 removed multi-profile-per-user so
    the quota path itself is unreachable."""


# ---------------------------------------------------------------------------
# Profile dataclass
# ---------------------------------------------------------------------------


@dataclass
class Profile:
    id: str
    user_id: str
    name: str
    is_default: bool
    is_active: bool
    created_at: datetime
    description: str = ""
    slug: str = ""


# ---------------------------------------------------------------------------
# Protocol — the contract every store implementation honors
# ---------------------------------------------------------------------------


class ProfileStore(Protocol):
    def ensure_default_profile(self, *, user_id: str) -> Profile: ...
    def create_profile(
        self,
        *,
        user_id: str,
        name: str,
        description: str = "",
        slug: str | None = None,
        max_profiles_per_user: int | None = None,
    ) -> Profile: ...
    def update_profile(
        self,
        profile_id: str,
        *,
        user_id: str,
        description: str | None = None,
        slug: str | None = None,
    ) -> Profile | None: ...
    def get_profile(self, profile_id: str) -> Profile | None: ...
    def get_default_profile(self, *, user_id: str) -> Profile | None: ...
    def list_profiles(
        self, *, user_id: str, include_inactive: bool = False
    ) -> list[Profile]: ...
    def list_all_active_profiles(self) -> list[Profile]: ...
    def rename_profile(
        self, profile_id: str, *, user_id: str, new_name: str
    ) -> Profile | None: ...
    def set_default(self, *, user_id: str, profile_id: str) -> bool: ...
    def delete_profile(self, profile_id: str, *, user_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Internal helpers (slug validation + name validation)
# ---------------------------------------------------------------------------


def _generate_profile_id() -> str:
    """**Deprecated** — Charter v2 §简化原则: 1 user = 1 profile.

    profile_id is always equal to ``users.username``. This generator
    existed for the multi-profile-per-user feature which v2 removes.
    Kept as a hard-fail (rather than deleted) so any lingering call
    site surfaces immediately.
    """
    raise NotImplementedError(
        "Charter v2 §简化原则: 1 user = 1 profile. "
        "profile_id must equal users.username; do not generate hex IDs. "
        "See docs/2026-05-05-hermes-crm-first-principles-v2.md."
    )


def _validate_name(name: str) -> str:
    stripped = (name or "").strip()
    if not stripped:
        raise ValueError("profile name must be non-empty")
    if len(stripped) > 64:
        raise ValueError("profile name must be 64 characters or fewer")
    return stripped


# Charter v2: profile slug uses the same shape as users.username (validated
# via ``ck_users_username_slug`` DB constraint). Anything passed for the
# ``slug`` field of update_profile / create_profile must match this regex.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _slugify(value: str) -> str:
    """Reduce *value* to a slug-safe lowercase string.

    Non-alphanumeric runs collapse to a single ``-``. Leading/trailing
    dashes are stripped. If nothing is left (e.g. all CJK / punctuation),
    returns ``"agent"`` — caller dedup-loops via :func:`_unique_slug`.
    """
    s = (value or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "agent"


def _validate_slug(slug: str) -> str:
    s = (slug or "").strip()
    if not _SLUG_RE.match(s):
        raise ValueError(
            f"slug {slug!r} must match {_SLUG_RE.pattern!r} "
            f"(matches Charter v2 ``ck_users_username_slug`` shape)"
        )
    return s


def _unique_slug(taken: set[str], desired: str, *, max_tries: int = 100) -> str:
    """Find a slug not in *taken* by appending a numeric suffix to
    *desired*. Stops after *max_tries* and falls back to a random hex
    suffix to guarantee termination.
    """
    if desired not in taken:
        return desired
    for i in range(2, max_tries + 1):
        candidate = f"{desired}-{i}"
        if candidate not in taken:
            return candidate
    return f"{desired}-{secrets.token_hex(3)}"


__all__ = [
    "DEFAULT_PROFILE_NAME",
    "Profile",
    "ProfileStore",
    "CannotDeleteDefaultProfileError",
    "ProfileQuotaExceededError",
    "_generate_profile_id",
    "_validate_name",
    "_validate_slug",
    "_slugify",
    "_unique_slug",
]
