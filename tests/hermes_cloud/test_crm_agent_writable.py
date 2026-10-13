"""Agent vs manual write scope for CRM records.

The ``readonly`` flag on a CRM field restricts ONLY the SPA's manual
create / edit forms. It is not an agent restriction: an agent / automation
write (``Save`` with ``source`` starting ``agent:``) may set ANY declared
field — including readonly business fields (``leads.convertedstatus``),
audit columns (``created_at``) and rollup columns — because correcting
bad data in any column is part of the agent's job.

Regression coverage for the ``leads.convertedstatus`` bug: a readonly
field was un-writable by the agent's ``crm_write`` path even though it
has no other writer.

DB-backed; skipped when ``hermes_crm_dev`` is unreachable — same policy
as the records / RBAC suites. Run with ``HERMES_TESTS_REAL_PG=1``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure hermes/ is importable (parity with dev_server.py / other suites).
_HERMES_DIR = Path(__file__).resolve().parents[2] / "hermes"
if _HERMES_DIR.is_dir() and str(_HERMES_DIR) not in sys.path:
    sys.path.insert(0, str(_HERMES_DIR))


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


@pytest.fixture(scope="module")
def ids():
    """Resolve module ids + the seed admin user id once."""
    from hermes.crm.db import db_session
    from hermes.crm.models.modules_db import ModulesDb

    with db_session() as db:
        mods = ModulesDb(db)
        leads = mods.get_module_by_name("leads")
        quotes = mods.get_module_by_name("quotes")
        admin = db.fetchone("SELECT id FROM users WHERE username = 'admin' LIMIT 1")
    assert leads and quotes, "leads/quotes module missing — run the CRM seed"
    assert admin, "admin user missing — run the CRM seed"
    return {
        "leads_module_id": int(leads["id"]),
        "quotes_module_id": int(quotes["id"]),
        "admin_id": int(admin["id"]),
    }


@pytest.fixture
def test_lead(ids):
    """Create a throwaway lead, yield its id, hard-delete it after."""
    from hermes.crm.db import db_session
    from hermes.crm.save import Save

    with db_session() as db:
        rid = (
            Save(db)
            .set_module(ids["leads_module_id"])
            .set_user(ids["admin_id"], True)
            .set_source("agent:test:test")
            .insert({"leads_name": "PYTEST_agent_writable_probe"})
        )
        try:
            yield int(rid)
        finally:
            db.execute("DELETE FROM leads WHERE leads_id = %s", (rid,))


@pytest.fixture
def test_quote(ids):
    """Create a throwaway quote, yield its id, hard-delete it after."""
    from hermes.crm.db import db_session
    from hermes.crm.save import Save

    with db_session() as db:
        rid = (
            Save(db)
            .set_module(ids["quotes_module_id"])
            .set_user(ids["admin_id"], True)
            .set_source("agent:test:test")
            .insert({"quotes_name": "PYTEST_agent_writable_probe"})
        )
        try:
            yield int(rid)
        finally:
            db.execute("DELETE FROM quotes WHERE quotes_id = %s", (rid,))


def _agent_save(db, module_id, ids):
    from hermes.crm.save import Save

    return (
        Save(db)
        .set_module(module_id)
        .set_user(ids["admin_id"], True)
        .set_source("agent:test:test")
    )


def _manual_save(db, module_id, ids):
    from hermes.crm.save import Save

    return Save(db).set_module(module_id).set_user(ids["admin_id"], True)


# ---------------------------------------------------------------------------
# Agent path — may write ANY field
# ---------------------------------------------------------------------------


def test_agent_can_write_spa_readonly_business_field(ids, test_lead):
    """leads.convertedstatus is readonly in the SPA but agent-writable."""
    from hermes.crm.db import db_session

    with db_session() as db:
        _agent_save(db, ids["leads_module_id"], ids).set_record_id(
            test_lead
        ).update({"convertedstatus": "converted"})
        row = db.fetchone(
            "SELECT convertedstatus FROM leads WHERE leads_id = %s", (test_lead,)
        )
    assert row["convertedstatus"] == "converted"


def test_agent_can_write_audit_field(ids, test_lead):
    """Audit columns (created_at) are agent-writable — the agent must be
    able to correct any column, timestamps included."""
    from hermes.crm.db import db_session

    with db_session() as db:
        _agent_save(db, ids["leads_module_id"], ids).set_record_id(
            test_lead
        ).update({"created_at": "2020-06-15 08:00:00"})
        row = db.fetchone(
            "SELECT created_at FROM leads WHERE leads_id = %s", (test_lead,)
        )
    assert str(row["created_at"]).startswith("2020-06-15")


def test_agent_can_write_rollup_field(ids, test_quote):
    """Rollup/computed columns (quotes.total_quantity) are agent-writable."""
    from hermes.crm.db import db_session

    with db_session() as db:
        _agent_save(db, ids["quotes_module_id"], ids).set_record_id(
            test_quote
        ).update({"total_quantity": 88})
        row = db.fetchone(
            "SELECT total_quantity FROM quotes WHERE quotes_id = %s", (test_quote,)
        )
    assert int(row["total_quantity"]) == 88


def test_agent_unknown_field_reports_nonexistent(ids, test_lead):
    """A genuinely non-existent field is still hard-rejected — and the
    error must say it doesn't exist (not that it's readonly)."""
    from hermes.crm.db import db_session
    from hermes.crm.save import FieldValidationException

    with db_session() as db:
        with pytest.raises(FieldValidationException) as exc:
            _agent_save(db, ids["leads_module_id"], ids).set_record_id(
                test_lead
            ).update({"totally_bogus_xyz": "x"})
    msg = exc.value.message
    assert "不存在" in msg
    assert "__CLARIFY__" in msg


# ---------------------------------------------------------------------------
# Manual SPA path — readonly fields stay locked
# ---------------------------------------------------------------------------


def test_manual_cannot_write_spa_readonly_field(ids, test_lead):
    """The SPA manual form may not write convertedstatus — and the error
    must report it as readonly, NOT as nonexistent."""
    from hermes.crm.db import db_session
    from hermes.crm.save import FieldValidationException

    with db_session() as db:
        with pytest.raises(FieldValidationException) as exc:
            _manual_save(db, ids["leads_module_id"], ids).set_record_id(
                test_lead
            ).update({"convertedstatus": "failed"})
    msg = exc.value.message
    assert "只读" in msg
    assert "不存在" not in msg
    assert "__CLARIFY__" not in msg


def test_manual_editable_set_excludes_readonly(ids):
    """Sanity: the SPA-editable set still excludes readonly fields,
    while get_module_fields (the agent set) includes them."""
    from hermes.crm.db import db_session
    from hermes.crm.models.module_fields_db import ModuleFieldsDb

    with db_session() as db:
        mf = ModuleFieldsDb(db)
        mid = ids["leads_module_id"]
        editable = {f["field_name"] for f in mf.get_editable_fields(mid)}
        all_fields = {f["field_name"] for f in mf.get_module_fields(mid)}
    assert "convertedstatus" in all_fields
    assert "convertedstatus" not in editable
    assert "created_at" in all_fields
    assert "created_at" not in editable
