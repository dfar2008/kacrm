"""HMAC signing for the cloud → profile SPECIALIST.md catalog (v3.3.2c).

Why this module exists
----------------------
v3.3.2a (overlay_resolver) ships three layers — profile_local, system_shared,
bundled. The ``system_shared`` layer (``~/.hermes/system_specialists/``) is
written by ``profile_manager`` at process spawn time from a cloud-authoritative
catalog. Without provenance, a malicious profile (or stale cache, or local
edit) could inject a SPECIALIST.md that gets picked up by the resolver as if
it came from cloud.

This module provides the signing and verification primitives so bootstrap
can prove "this catalog entry came from cloud and hasn't been tampered
with locally" before trusting it.

Key lifecycle
-------------
* **Current key** comes from env ``HERMES_SPECIALIST_CATALOG_SIGNING_KEY``.
  32-byte random secret, base64 encoded. NOT reused with
  ``HERMES_CLOUD_INTERNAL_KEY`` (per v3.3 决策 11) — separating the two
  means a leaked internal-key doesn't grant the ability to forge catalog
  entries (which would propagate to every profile's overlay).
* **Historical keys** come from ``HERMES_SPECIALIST_CATALOG_SIGNING_KEY_PREV_1``
  through ``..._PREV_3``. Up to N=3 are honored on verify so a key rotation
  doesn't immediately invalidate every profile's existing
  ``.hash.signed`` files.
* **Key id** is derived as the first 8 hex chars of ``sha256(key)``, attached
  to every signature. On verify we look up the matching key by id; if no
  match, the signature is rejected (NOT silently fall-back-to-current).
* **Fail-fast** on missing current key: ``get_current_signing_key()`` raises
  ``RuntimeError``. Cloud lifespan calls it at boot so a missing key shows
  up immediately rather than at first ``/api/internal/specialist/list``
  request.

Generating a key
----------------
::

    openssl rand -base64 32

Put the result in ``.env.local`` (dev) or a K8s secret (prod) under
``HERMES_SPECIALIST_CATALOG_SIGNING_KEY``. To rotate, move the old value
into ``HERMES_SPECIALIST_CATALOG_SIGNING_KEY_PREV_1`` and put a new one
in ``HERMES_SPECIALIST_CATALOG_SIGNING_KEY``.

What this module does NOT do
----------------------------
* No persistence of the secret itself — only ``key_id`` ever leaves
  process memory. The on-disk ``.hash.signed`` file is
  ``{hash, signature, key_id}``.
* No envelope encryption — payloads are plaintext SPECIALIST.md content;
  the signature only proves provenance/integrity, not confidentiality.
* No TTL — rotation handles freshness.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os

logger = logging.getLogger(__name__)


# Env var names — kept as module-level constants so tests / docs / scripts
# can reference them without typos.
ENV_CURRENT = "HERMES_SPECIALIST_CATALOG_SIGNING_KEY"
ENV_PREV_PATTERN = "HERMES_SPECIALIST_CATALOG_SIGNING_KEY_PREV_{n}"
MAX_HISTORICAL_KEYS = 3


def _decode_b64_key(raw: str) -> bytes:
    """Decode a base64-encoded key. Strips whitespace.

    Raises ``ValueError`` on bad encoding rather than silently returning
    a different-length secret.
    """
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("signing key is empty")
    try:
        return base64.b64decode(cleaned, validate=True)
    except Exception as exc:
        raise ValueError(f"signing key not valid base64: {exc}") from exc


def derive_key_id(key: bytes) -> str:
    """Stable derivation of an 8-char lowercase hex key_id from a raw key.

    Same key in → same id out. We pin the algorithm (sha256 first-8-hex)
    so on-disk ``.hash.signed`` files stay valid across cloud restarts and
    code refactors.
    """
    if not isinstance(key, (bytes, bytearray)):
        raise TypeError("key must be bytes")
    return hashlib.sha256(bytes(key)).hexdigest()[:8]


def get_current_signing_key() -> tuple[str, bytes]:
    """Return ``(key_id, key_bytes)`` for the current signing key.

    Reads env on every call (cheap; no module-level cache) so tests and
    rotation drills can monkeypatch ``os.environ`` without restarting.

    Raises ``RuntimeError`` when ``HERMES_SPECIALIST_CATALOG_SIGNING_KEY``
    is missing or invalid — cloud bootstrap should call this at startup so
    misconfiguration surfaces immediately, not at first signing call.
    """
    raw = os.environ.get(ENV_CURRENT)
    if not raw:
        raise RuntimeError(
            f"{ENV_CURRENT} is not set. Generate one with "
            "`openssl rand -base64 32` and put it in your .env.local "
            "(dev) or K8s secret (prod). See "
            "hermes_cloud/app/security/catalog_signing.py for details."
        )
    key = _decode_b64_key(raw)
    return derive_key_id(key), key


def get_historical_signing_keys() -> list[tuple[str, bytes]]:
    """Return ``[(key_id, key_bytes), ...]`` for up to N=3 previous keys.

    Order: PREV_1, PREV_2, PREV_3. Missing or empty slots are silently
    skipped (so ops can populate just the slots they need). Bad-encoding
    slots ARE logged + skipped — better to keep verifying with the
    healthy keys than crash the whole bootstrap.
    """
    out: list[tuple[str, bytes]] = []
    for n in range(1, MAX_HISTORICAL_KEYS + 1):
        raw = os.environ.get(ENV_PREV_PATTERN.format(n=n))
        if not raw:
            continue
        try:
            key = _decode_b64_key(raw)
        except ValueError as exc:
            logger.warning(
                "catalog_signing.historical_key_invalid n=%d err=%s", n, exc,
            )
            continue
        out.append((derive_key_id(key), key))
    return out


def sign_spec(payload_hash: str, key: bytes) -> str:
    """Return the hex HMAC-SHA256 of ``payload_hash`` under ``key``.

    ``payload_hash`` is expected to itself be a stable hash of the spec
    body (e.g. sha256 of canonical JSON) — signing the hash rather than
    the raw content lets verifiers store ``{hash, signature}`` and
    re-verify locally without keeping the full body around.
    """
    if not isinstance(payload_hash, str) or not payload_hash:
        raise ValueError("payload_hash must be a non-empty string")
    if not isinstance(key, (bytes, bytearray)):
        raise TypeError("key must be bytes")
    return hmac.new(
        bytes(key), payload_hash.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def verify_spec_signature(
    payload_hash: str, signature: str, key_id: str,
) -> bool:
    """Verify ``signature`` for ``payload_hash`` against the key matching
    ``key_id`` (current or historical).

    Returns ``True`` only when:

    1. We can find a key with the given ``key_id`` (current first, then
       PREV_1..3 in order), AND
    2. The signature recomputed with that key matches via constant-time
       compare.

    Returns ``False`` when:

    * Current key is missing (env unset) — verification is impossible, so
      nothing should be trusted.
    * No key matches ``key_id`` — older than rotation window, treat as
      unverifiable rather than silently re-sign with current key.
    * Signature mismatch.
    """
    if not signature or not key_id:
        return False

    candidates: list[tuple[str, bytes]] = []
    try:
        candidates.append(get_current_signing_key())
    except RuntimeError:
        # Current key missing — we cannot verify anything reliably.
        # Don't even fall back to historical, because the operator
        # state is broken and we want bootstrap to log + skip.
        logger.warning("catalog_signing.verify_no_current_key key_id=%s", key_id)
        return False
    candidates.extend(get_historical_signing_keys())

    matched: bytes | None = None
    for kid, key in candidates:
        if hmac.compare_digest(kid, key_id):
            matched = key
            break
    if matched is None:
        # key_id older than retention window, or never seen.
        return False

    expected = sign_spec(payload_hash, matched)
    return hmac.compare_digest(expected, signature)


__all__ = [
    "ENV_CURRENT",
    "ENV_PREV_PATTERN",
    "MAX_HISTORICAL_KEYS",
    "derive_key_id",
    "get_current_signing_key",
    "get_historical_signing_keys",
    "sign_spec",
    "verify_spec_signature",
]
