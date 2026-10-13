"""Tests for :mod:`hermes_cloud.app.bridge.events`.

Phase 1g. Covers :func:`emit_business_write` — both its pure-function
bits (``_values_equal``) and its DB-side effects (changelog + interactions).
The insert/update tests in ``test_interactions.py`` still exercise the
hook end-to-end through :class:`Save`; these tests additionally cover
the *non-Save* caller path that Phase 2's feishu bot / Agent writer
will take.
"""
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


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_values_equal_none_cases():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(None, None) is True
    assert _values_equal(None, "") is False
    assert _values_equal("", None) is False


def test_values_equal_numeric_coercion():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal(1, 1.0) is True
    assert _values_equal(1, "1") is True
    assert _values_equal("1.5", 1.5) is True
    assert _values_equal(1, 2) is False


def test_values_equal_string_compare():
    from hermes_cloud.app.bridge.events import _values_equal

    assert _values_equal("abc", "abc") is True
    assert _values_equal("abc", "abd") is False


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_id() -> int:
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row, "admin user missing — run seed"
    return int(row["id"])


@pytest.fixture(scope="module")
def accounts_module_id() -> int:
    """Any real ``collections`` id works — changelog.module_id has an FK.
    We use ``accounts`` since it's always present after seed and we never
    actually write to the ``accounts`` record table in these tests."""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM collections WHERE name = 'accounts' LIMIT 1"
        )
    assert row, "accounts collection missing — run seed"
    return int(row["id"])


def _interactions_for(module: str, record_id: int) -> list[dict]:
    from hermes.crm.db import db_session

    with db_session() as db:
        return db.fetchall(
            """
            SELECT kind, source, content, actors, subjects
            FROM interactions
            WHERE subjects @> %s::jsonb
            ORDER BY occurred_at ASC
            """,
            (f'[{{"module":"{module}","id":{int(record_id)}}}]',),
        )


def _changelog_for(module_id: int, record_id: int) -> list[dict]:
    from hermes.crm.db import db_session

    with db_session() as db:
        return db.fetchall(
            """
            SELECT field_name, field_label, field_type, old_value, new_value
            FROM changelog
            WHERE module_id = %s AND data_id = %s
            ORDER BY tracking_id ASC
            """,
            (int(module_id), int(record_id)),
        )


def _cleanup(module_id: int, probe_id: int) -> None:
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM interactions WHERE subjects @> %s::jsonb",
            (f'[{{"module":"probe","id":{int(probe_id)}}}]',),
        )
        db.execute(
            "DELETE FROM changelog WHERE module_id = %s AND data_id = %s",
            (int(module_id), int(probe_id)),
        )


def test_emit_business_write_create_writes_changelog_and_interaction(
    admin_id, accounts_module_id
):
    """Standalone caller: no Save involvement — just the hook."""
    from hermes_cloud.app.bridge.events import emit_business_write
    from hermes.crm.db import db_session

    probe_id = 2_000_000 + int(secrets.token_hex(3), 16)
    field_catalog = [
        {"field_name": "alpha", "field_label": "Alpha", "field_type": "text"},
        {"field_name": "beta", "field_label": "Beta", "field_type": "number"},
    ]

    try:
        with db_session() as db:
            emit_business_write(
                db=db,
                module_id=accounts_module_id,
                module_name="probe",
                record_id=probe_id,
                op="create",
                old={},
                new={"alpha": "one", "beta": 42},
                user_id=admin_id,
                source="manual",
                field_catalog=field_catalog,
            )

        rows = _interactions_for("probe", probe_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "action"
        assert row["source"] == "manual"
        assert row["content"]["op"] == "create"
        assert set(row["content"]["fields"]) == {"alpha", "beta"}

        logs = _changelog_for(accounts_module_id, probe_id)
        assert len(logs) == 2
        by_name = {l["field_name"]: l for l in logs}
        assert by_name["alpha"]["field_type"] == "text"
        assert by_name["alpha"]["field_label"] == "Alpha"
        assert by_name["beta"]["field_type"] == "number"
    finally:
        _cleanup(accounts_module_id, probe_id)


def test_emit_business_write_update_narrows_to_changed_fields(
    admin_id, accounts_module_id
):
    from hermes_cloud.app.bridge.events import emit_business_write
    from hermes.crm.db import db_session

    probe_id = 2_100_000 + int(secrets.token_hex(3), 16)
    catalog = [
        {"field_name": "x", "field_label": "X", "field_type": "text"},
        {"field_name": "y", "field_label": "Y", "field_type": "text"},
    ]

    try:
        with db_session() as db:
            emit_business_write(
                db=db,
                module_id=accounts_module_id,
                module_name="probe",
                record_id=probe_id,
                op="update",
                old={"x": "same", "y": "was"},
                new={"x": "same", "y": "now"},
                user_id=admin_id,
                field_catalog=catalog,
            )

        rows = _interactions_for("probe", probe_id)
        assert len(rows) == 1
        # Only y actually changed — x should be filtered out.
        assert rows[0]["content"]["fields"] == ["y"]

        logs = _changelog_for(accounts_module_id, probe_id)
        assert len(logs) == 1
        assert logs[0]["field_name"] == "y"
        assert logs[0]["old_value"] == "was"
        assert logs[0]["new_value"] == "now"
    finally:
        _cleanup(accounts_module_id, probe_id)


def test_emit_business_write_update_noop_emits_nothing(
    admin_id, accounts_module_id
):
    """If every field value equals its old counterpart, no interaction
    is written — same behaviour as the REST /update path."""
    from hermes_cloud.app.bridge.events import emit_business_write
    from hermes.crm.db import db_session

    probe_id = 2_200_000 + int(secrets.token_hex(3), 16)

    try:
        with db_session() as db:
            emit_business_write(
                db=db,
                module_id=accounts_module_id,
                module_name="probe",
                record_id=probe_id,
                op="update",
                old={"x": "same", "y": "same"},
                new={"x": "same", "y": "same"},
                user_id=admin_id,
            )

        assert _interactions_for("probe", probe_id) == []
        assert _changelog_for(accounts_module_id, probe_id) == []
    finally:
        _cleanup(accounts_module_id, probe_id)


def test_emit_business_write_honours_source_stamp(
    admin_id, accounts_module_id
):
    from hermes_cloud.app.bridge.events import emit_business_write
    from hermes.crm.db import db_session

    probe_id = 2_300_000 + int(secrets.token_hex(3), 16)

    try:
        with db_session() as db:
            emit_business_write(
                db=db,
                module_id=accounts_module_id,
                module_name="probe",
                record_id=probe_id,
                op="create",
                old={},
                new={"alpha": "v"},
                user_id=admin_id,
                source="agent:default/field-extractor",
            )

        rows = _interactions_for("probe", probe_id)
        assert len(rows) == 1
        assert rows[0]["source"] == "agent:default/field-extractor"
    finally:
        _cleanup(accounts_module_id, probe_id)


def test_emit_business_write_without_field_catalog_still_logs(
    admin_id, accounts_module_id
):
    """Absent a catalog, changelog rows get best-effort 'text' typing —
    still valid audit data, just less descriptive."""
    from hermes_cloud.app.bridge.events import emit_business_write
    from hermes.crm.db import db_session

    probe_id = 2_400_000 + int(secrets.token_hex(3), 16)

    try:
        with db_session() as db:
            emit_business_write(
                db=db,
                module_id=accounts_module_id,
                module_name="probe",
                record_id=probe_id,
                op="create",
                old={},
                new={"only_field": "hello"},
                user_id=admin_id,
            )

        logs = _changelog_for(accounts_module_id, probe_id)
        assert len(logs) == 1
        assert logs[0]["field_name"] == "only_field"
        assert logs[0]["field_label"] == "only_field"
        assert logs[0]["field_type"] == "text"
    finally:
        _cleanup(accounts_module_id, probe_id)
