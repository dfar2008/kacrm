"""0102 — schema-pin: attachments + attachment_links.

Pins the file-persistence + record-association tables added for the
"associate uploaded images/PDFs with auto-created records" feature.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from _db_url import resolve_database_url


@pytest.fixture(scope="module")
def sync_engine():
    url = resolve_database_url()
    if not url:
        pytest.skip("no CRM database URL configured")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"CRM database not reachable: {exc}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_attachments_columns(sync_engine):
    cols = {c["name"]: c for c in inspect(sync_engine).get_columns("attachments")}
    for name in (
        "owner_user_id", "profile_id", "conversation_id", "filename",
        "mime_type", "kind", "sha256", "size_bytes", "stored_path",
        "created_at", "deleted",
    ):
        assert name in cols, name
    assert cols["conversation_id"]["nullable"] is True
    assert cols["owner_user_id"]["nullable"] is False
    assert cols["sha256"]["nullable"] is False
    assert cols["stored_path"]["nullable"] is False


def test_attachment_links_columns(sync_engine):
    cols = {c["name"]: c for c in inspect(sync_engine).get_columns("attachment_links")}
    for name in (
        "attachment_id", "target_module", "target_record_id",
        "interaction_id", "created_at",
    ):
        assert name in cols, name
    assert cols["target_module"]["nullable"] is False
    assert cols["target_record_id"]["nullable"] is False


def test_attachment_indexes(sync_engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename IN ('attachments','attachment_links')"
        )).fetchall()
    names = {r[0] for r in rows}
    assert "ux_attachments_sha_owner" in names
    assert "ix_attachments_conversation" in names
    assert "ux_attlink" in names
    assert "ix_attlinks_target" in names
    assert "ix_attlinks_attachment" in names
