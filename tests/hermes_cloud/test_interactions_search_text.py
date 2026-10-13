from __future__ import annotations

import secrets

import pytest


def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _crm_reachable(), reason="hermes_crm_dev not reachable"
)


def test_emit_populates_search_text() -> None:
    from hermes.crm.db import db_session
    from hermes_cloud.app.interactions import emit

    probe_id = 1_000_000 + int(secrets.token_hex(3), 16)
    marker = f"st-{secrets.token_hex(4)}"

    uid = emit(
        kind="action",
        subjects=[{"module": "probe", "id": probe_id}],
        actors=[{"type": "user", "id": 1}],
        content={
            "summary": f"{marker} summary",
            "body": "body-value",
            "message": "msg-value",
        },
        source="manual",
    )
    try:
        with db_session() as db:
            row = db.fetchone(
                "SELECT search_text FROM interactions WHERE id = %s",
                (uid,),
            )
        assert row is not None
        search_text = row.get("search_text") or ""
        assert marker in search_text
        assert "body-value" in search_text
        assert "msg-value" in search_text
    finally:
        with db_session() as db:
            db.execute("DELETE FROM interactions WHERE id = %s", (uid,))


def test_search_text_matches_tsvector_query() -> None:
    from hermes.crm.db import db_session
    from hermes_cloud.app.interactions import emit

    probe_id = 1_000_000 + int(secrets.token_hex(3), 16)
    marker = f"needle{secrets.token_hex(4)}"

    uid = emit(
        kind="action",
        subjects=[{"module": "probe", "id": probe_id}],
        actors=[{"type": "user", "id": 1}],
        content={"text": f"alpha {marker} omega"},
        source="manual",
    )
    try:
        with db_session() as db:
            row = db.fetchone(
                """
                SELECT id
                  FROM interactions
                 WHERE id = %s
                   AND to_tsvector('simple', coalesce(search_text, ''))
                       @@ plainto_tsquery('simple', %s)
                """,
                (uid, marker),
            )
        assert row is not None and str(row["id"]) == uid
    finally:
        with db_session() as db:
            db.execute("DELETE FROM interactions WHERE id = %s", (uid,))
