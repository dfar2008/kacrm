"""Base contract for channel receivers + shared crypto helpers.

Every channel's signature verification ends up being one of three
families:

1. **Symmetric HMAC** over a canonical string (Slack, DingTalk) —
   :func:`hmac_sha256_hex` / :func:`hmac_sha256_b64`.
2. **Asymmetric (Ed25519)** signed by the platform (Discord) — left
   to the individual module (requires PyNaCl).
3. **Opaque token match** (Telegram) — :func:`constant_time_equals`.

Plus WeCom/DingTalk/Feishu's optional AES-CBC event encryption layer,
implemented in the receivers that need it.

All helpers are stateless and the verification functions raise
:class:`ChannelVerificationError` on failure so the API layer can
uniformly translate that into HTTP 401.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Optional

logger = logging.getLogger(__name__)


class ChannelVerificationError(Exception):
    """Raised when a signature / token / encryption check fails.

    The API layer returns HTTP 401 for this. The message text IS
    exposed to the caller — don't include secrets in it.
    """


# ---------------------------------------------------------------------------
# Shared crypto helpers
# ---------------------------------------------------------------------------

def constant_time_equals(a: str, b: str) -> bool:
    """HMAC-safe string comparison. Works on ``str`` or ``bytes``."""
    a_b = a.encode() if isinstance(a, str) else a
    b_b = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(a_b, b_b)


def hmac_sha256_hex(secret: str, message: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hmac_sha256_b64(secret: str, message: str) -> str:
    import base64
    digest = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def sha1_hex(*parts: str) -> str:
    """Sort-concat-SHA1 — used by WeCom / Feishu / DingTalk legacy.

    Concatenates the parts in lexicographic order then SHA1-hexes.
    """
    joined = "".join(sorted(parts))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Receiver contract
# ---------------------------------------------------------------------------

@dataclass
class ChannelConfig:
    """Opaque bundle of per-channel secrets pulled from :class:`AppConfig`
    or env. Each receiver defines its own expected keys (stored in
    ``data``) — this dataclass just normalizes "missing config".
    """

    enabled: bool = False
    data: Optional[Dict[str, Any]] = None

    def require(self, key: str) -> str:
        d = self.data or {}
        v = d.get(key)
        if not v:
            raise ChannelVerificationError(
                f"channel config missing required key: {key}"
            )
        return str(v)

    def get(self, key: str, default: Any = None) -> Any:
        return (self.data or {}).get(key, default)


class ChannelReceiver:
    """Abstract base — subclass per channel.

    Subclasses implement :meth:`verify_and_extract` which takes the raw
    request (headers + body) and returns the decrypted/normalized
    payload ready to hand to the matching
    :class:`TouchpointAdapter`. The HTTP router is centralised in
    :mod:`channels.api` — receivers should stay transport-agnostic.
    """

    source: ClassVar[str] = ""                 # 'channel:wecom' etc.
    touchpoint_source: ClassVar[str] = ""      # typically same as source
    needs_decryption: ClassVar[bool] = False

    def load_config(self, cfg: Any) -> ChannelConfig:
        """Pull this channel's secrets off an :class:`AppConfig`.

        Default: looks for ``<source_key>_*`` fields on the config.
        Override when the mapping is non-trivial.
        """
        raise NotImplementedError

    def verify_and_extract(
        self,
        *,
        headers: Dict[str, str],
        body: bytes,
        query: Dict[str, str],
        cfg: ChannelConfig,
    ) -> Optional[Dict[str, Any]]:
        """Return the decrypted payload, or ``None`` when the request is
        a URL-verification challenge that the router should short-circuit
        via :meth:`challenge_response` instead of touchpoint ingest.

        Raises :class:`ChannelVerificationError` if signatures don't
        check out.
        """
        raise NotImplementedError

    def challenge_response(
        self,
        *,
        body: bytes,
        query: Dict[str, str],
        cfg: ChannelConfig,
    ) -> Optional[Dict[str, Any]]:
        """Return a JSON-serialisable response for URL-verification
        callbacks. Return ``None`` if this channel has no challenge
        flow (Slack / Telegram / Discord / email).
        """
        return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: Dict[str, ChannelReceiver] = {}


def register_receiver(receiver: ChannelReceiver) -> None:
    if not receiver.source:
        raise ValueError(f"{type(receiver).__name__}.source must be set")
    _registry[receiver.source] = receiver


def get_receiver(source: str) -> Optional[ChannelReceiver]:
    key = source if source.startswith("channel:") else f"channel:{source}"
    return _registry.get(key) or _registry.get(source)


def list_receivers() -> list[str]:
    return sorted(_registry.keys())


__all__ = [
    "ChannelConfig",
    "ChannelReceiver",
    "ChannelVerificationError",
    "constant_time_equals",
    "hmac_sha256_hex",
    "hmac_sha256_b64",
    "sha1_hex",
    "register_receiver",
    "get_receiver",
    "list_receivers",
]
