"""Persist uploaded files + associate them to CRM records by conversation.

Two write paths:
  persist_uploads()               — at upload (/api/chat/attachments), bytes in
                                     hand. Dedups by (owner, sha256).
  link_conversation_attachments() — at emit_business_write, when a record is
                                     born in the same conversation.
And two reads: list_for_record(), get_attachment().

DB access mirrors hermes.crm.db.DbWrapper:
  fetchone(sql, args) -> dict|None   (does NOT commit)
  fetchall(sql, args) -> list[dict]
  execute(sql, args)  -> rowcount    (commits by default)
"""
from __future__ import annotations

import hashlib
import mimetypes
from typing import Any, Dict, List, Optional, Tuple

from hermes_cloud.app.attachments.storage import LocalDiskStore

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _ext(filename: str) -> str:
    i = (filename or "").rfind(".")
    return filename[i + 1:].lower() if i >= 0 else ""


def _kind(filename: str, content_type: str) -> str:
    if _ext(filename) in IMAGE_EXTS or (content_type or "").startswith("image/"):
        return "image"
    return "document"


def persist_uploads(
    db,
    *,
    raw_files: List[Tuple[str, bytes, str]],
    owner_user_id: int,
    profile_id: str,
    conversation_id: str,
    store: Optional[LocalDiskStore] = None,
) -> List[int]:
    """Persist each ``(filename, data, content_type)``. Reuses an existing row
    when (owner, sha256) already present (dedup). Returns attachment ids aligned
    with ``raw_files``."""
    store = store or LocalDiskStore()
    ids: List[int] = []
    for filename, data, content_type in raw_files:
        sha = hashlib.sha256(data).hexdigest()
        existing = db.fetchone(
            "SELECT id FROM attachments "
            "WHERE owner_user_id = %s AND sha256 = %s AND deleted = false",
            (owner_user_id, sha),
        )
        if existing and existing.get("id") is not None:
            aid = int(existing["id"])
            # Backfill conversation_id if the earlier upload lacked one, so the
            # reused file still associates to records created in this chat.
            db.execute(
                "UPDATE attachments SET conversation_id = %s "
                "WHERE id = %s AND (conversation_id IS NULL OR conversation_id = '')",
                (conversation_id, aid),
            )
            ids.append(aid)
            continue
        ext = _ext(filename)
        rel = store.put(sha256=sha, ext=ext, data=data, profile_id=str(profile_id))
        mime = (
            content_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        row = db.fetchone(
            "INSERT INTO attachments "
            "(owner_user_id, profile_id, conversation_id, filename, mime_type, "
            " kind, sha256, size_bytes, stored_path) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                owner_user_id, str(profile_id), conversation_id, filename, mime,
                _kind(filename, content_type), sha, len(data), rel,
            ),
        )
        db._maybe_commit(True)  # fetchone() doesn't commit  # pylint: disable=protected-access
        ids.append(int(row["id"]))
    return ids


def link_conversation_attachments(
    db,
    *,
    conversation_id: str,
    target_module: str,
    target_record_id: int,
    interaction_id: Optional[str] = None,
) -> int:
    """Link every attachment of this conversation to the record just written.
    Idempotent via the ux_attlink UNIQUE constraint. Returns rows inserted."""
    if not conversation_id:
        return 0
    return db.execute(
        "INSERT INTO attachment_links "
        "(attachment_id, target_module, target_record_id, interaction_id) "
        "SELECT a.id, %s, %s, %s FROM attachments a "
        "WHERE a.conversation_id = %s AND a.deleted = false "
        "ON CONFLICT ON CONSTRAINT ux_attlink DO NOTHING",
        (target_module, int(target_record_id), interaction_id, conversation_id),
    )


def list_for_record(db, *, module: str, record_id: int) -> List[Dict[str, Any]]:
    return db.fetchall(
        "SELECT a.id, a.filename, a.mime_type, a.kind, a.size_bytes, a.created_at "
        "FROM attachment_links l JOIN attachments a ON a.id = l.attachment_id "
        "WHERE l.target_module = %s AND l.target_record_id = %s "
        "  AND a.deleted = false "
        "ORDER BY a.created_at",
        (module, int(record_id)),
    )


def get_attachment(db, *, attachment_id: int) -> Optional[Dict[str, Any]]:
    return db.fetchone(
        "SELECT id, owner_user_id, profile_id, filename, mime_type, kind, "
        "stored_path FROM attachments WHERE id = %s AND deleted = false",
        (int(attachment_id),),
    )
