"""Attachment blob storage — local disk now, swappable for object storage.

``stored_path`` is RELATIVE
(``profiles/{profile_id}/attachments/{sha[:2]}/{sha}.{ext}``) so the physical
root can move (or become a Tencent COS bucket) without rewriting any rows.
A future ``CosStore`` implements the same ``AttachmentStore`` protocol and the
calling code stays untouched.
"""
from __future__ import annotations

import os
from typing import BinaryIO, Optional, Protocol


def _default_root() -> str:
    """Physical storage root. Mirrors the profile-dir convention
    (``~/.hermes`` unless ``hermes_home_root`` overrides it)."""
    from hermes_cloud.app.config import AppConfig

    root = getattr(AppConfig(), "hermes_home_root", None) or "~/.hermes"
    return os.path.expanduser(root)


class AttachmentStore(Protocol):
    def put(self, *, sha256: str, ext: str, data: bytes, profile_id: str) -> str: ...

    def open(self, stored_path: str) -> BinaryIO: ...

    def exists(self, stored_path: str) -> bool: ...


class LocalDiskStore:
    """Content-addressed local-disk store. ``put`` is idempotent — the same
    bytes (same sha) never get written twice."""

    def __init__(self, root: Optional[str] = None) -> None:
        self._root = root or _default_root()

    def _rel(self, sha256: str, ext: str, profile_id: str) -> str:
        bucket = sha256[:2]
        suffix = f".{ext}" if ext else ""
        return f"profiles/{profile_id}/attachments/{bucket}/{sha256}{suffix}"

    def _abs(self, rel: str) -> str:
        return os.path.join(self._root, rel)

    def put(self, *, sha256: str, ext: str, data: bytes, profile_id: str) -> str:
        rel = self._rel(sha256, ext, profile_id)
        dst = self._abs(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):  # content-addressed → idempotent
            tmp = f"{dst}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dst)
        return rel

    def exists(self, stored_path: str) -> bool:
        return os.path.exists(self._abs(stored_path))

    def open(self, stored_path: str) -> BinaryIO:
        return open(self._abs(stored_path), "rb")
