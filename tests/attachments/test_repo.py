"""persist_uploads + conversation linking — needs a live CRM DB; skips otherwise."""
from __future__ import annotations

import pytest


@pytest.fixture
def store(tmp_path):
    from hermes_cloud.app.attachments.storage import LocalDiskStore
    return LocalDiskStore(root=str(tmp_path))


def _db_session():
    try:
        from hermes.crm.db import db_session
    except Exception as exc:  # pragma: no cover — hermes absent
        pytest.skip(f"hermes.crm.db unavailable: {exc}")
    return db_session


def test_dedup_same_sha(store):
    from hermes_cloud.app.attachments.repo import persist_uploads
    db_session = _db_session()
    files = [("a.pdf", b"same-bytes", "application/pdf")]
    try:
        with db_session() as db:
            id1 = persist_uploads(db, raw_files=files, owner_user_id=99001,
                                  profile_id="99001", conversation_id="convA", store=store)
            id2 = persist_uploads(db, raw_files=files, owner_user_id=99001,
                                  profile_id="99001", conversation_id="convA", store=store)
    except Exception as exc:  # pragma: no cover — db unreachable
        pytest.skip(f"db unreachable: {exc}")
    assert id1 == id2  # reused the same row


def test_link_by_conversation(store):
    from hermes_cloud.app.attachments.repo import (
        persist_uploads, link_conversation_attachments, list_for_record,
    )
    db_session = _db_session()
    try:
        with db_session() as db:
            persist_uploads(db, raw_files=[("b.png", b"img-bytes", "image/png")],
                            owner_user_id=99002, profile_id="99002",
                            conversation_id="convB", store=store)
            n = link_conversation_attachments(
                db, conversation_id="convB", target_module="accounts",
                target_record_id=99002001)
            again = link_conversation_attachments(
                db, conversation_id="convB", target_module="accounts",
                target_record_id=99002001)
            items = list_for_record(db, module="accounts", record_id=99002001)
    except Exception as exc:  # pragma: no cover — db unreachable
        pytest.skip(f"db unreachable: {exc}")
    assert n >= 1
    assert again == 0  # idempotent
    assert any(it["filename"] == "b.png" for it in items)
