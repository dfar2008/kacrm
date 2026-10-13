"""Backend logic for picklist delete + label rename.

The full HTTP integration test (login + cookies + FastAPI TestClient)
ran into the dev DB's password-auth mismatch in this environment; here
we exercise the endpoint bodies directly by calling them as plain
Python functions through their dispatcher (no FastAPI middleware
involved). The dispatcher is registered via ``register_crm_routes`` on
an ephemeral FastAPI app; we pull the handlers off ``app.routes`` and
invoke them with a stubbed ``user`` + the real DB.

Coverage:
  * delete a system option (won/lost) → HTTPException(409)
  * delete a non-existent option → HTTPException(404)
  * delete an in-use option → HTTPException(409) with a count
  * rename a system option's label → ok (value is locked, label isn't)
  * rename an unknown option → HTTPException(404)
  * rename to empty string → HTTPException(400)
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, HTTPException

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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


def _endpoint_handlers():
    """Return the picklist-option route callables in a dict so each
    test can grab the one it needs. ``build_router`` returns a
    pre-prefixed router; we strip the ``/crm`` prefix here so paths
    line up with the literal strings used in tests."""
    from hermes_cloud.app.crm.routes import build_router
    router = build_router()
    by_path: dict[tuple[str, str], object] = {}
    for route in router.routes:
        path = getattr(route, "path", "")
        if path.startswith("/crm"):
            path = path[len("/crm"):]
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            by_path[(method, path)] = route.endpoint
    return by_path


@pytest.fixture(scope="module")
def handlers():
    try:
        return _endpoint_handlers()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"could not register routes: {exc}")


@pytest.fixture
def admin_user():
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id, username FROM users WHERE username='admin' LIMIT 1"
        )
    return {"id": int(row["id"]), "username": row["username"]}


@pytest.fixture
def db_handle():
    from hermes.crm.db import db_session

    with db_session() as db:
        yield db


def test_delete_system_option_won_rejected_409(handlers, admin_user, db_handle):
    delete_opt = handlers[("DELETE", "/modules/{module_name}/fields/{field_name}/options/{value}")]
    with pytest.raises(HTTPException) as exc:
        delete_opt(
            module_name="potentials",
            field_name="milestone",
            value="won",
            reason=None,
            user=admin_user,
            db=db_handle,
        )
    assert exc.value.status_code == 409
    assert "system" in str(exc.value.detail).lower()


def test_delete_unknown_option_returns_404(handlers, admin_user, db_handle):
    delete_opt = handlers[("DELETE", "/modules/{module_name}/fields/{field_name}/options/{value}")]
    bogus = f"does_not_exist_{secrets.token_hex(3)}"
    with pytest.raises(HTTPException) as exc:
        delete_opt(
            module_name="potentials",
            field_name="milestone",
            value=bogus,
            reason=None,
            user=admin_user,
            db=db_handle,
        )
    assert exc.value.status_code == 404


def test_delete_in_use_option_rejected_409(handlers, admin_user, db_handle):
    """Seed a potential at 'closing', then try to delete 'closing' from
    the picklist → must 409 with an 'in use' message + count."""
    delete_opt = handlers[("DELETE", "/modules/{module_name}/fields/{field_name}/options/{value}")]
    pid_row = db_handle.fetchone(
        """
        INSERT INTO potentials (potentials_name, owner_by, milestone,
                                created_by, updated_by, amount)
        VALUES (%s, %s, 'closing', %s, %s, 0)
        RETURNING potentials_id
        """,
        (
            f"in-use-{secrets.token_hex(3)}",
            admin_user["id"], admin_user["id"], admin_user["id"],
        ),
    )
    pid = int(pid_row["potentials_id"])
    try:
        with pytest.raises(HTTPException) as exc:
            delete_opt(
                module_name="potentials",
                field_name="milestone",
                value="closing",
                reason=None,
                user=admin_user,
                db=db_handle,
            )
        assert exc.value.status_code == 409
        assert "in use" in str(exc.value.detail).lower()
    finally:
        db_handle.execute(
            "DELETE FROM potentials WHERE potentials_id = %s", (pid,)
        )


def test_rename_system_option_label_is_allowed(handlers, admin_user, db_handle):
    """`won` value is locked but label is editable; flip and flip back
    so other tests aren't affected."""
    update_opt = handlers[("PATCH", "/modules/{module_name}/fields/{field_name}/options/{value}")]
    new_label = f"赢单-{secrets.token_hex(2)}"
    result = update_opt(
        module_name="potentials",
        field_name="milestone",
        value="won",
        payload={"label": new_label},
        user=admin_user,
        db=db_handle,
    )
    assert result["ok"] is True
    assert result["label"] == new_label

    # restore
    update_opt(
        module_name="potentials",
        field_name="milestone",
        value="won",
        payload={"label": "赢单"},
        user=admin_user,
        db=db_handle,
    )


def test_rename_unknown_option_returns_404(handlers, admin_user, db_handle):
    update_opt = handlers[("PATCH", "/modules/{module_name}/fields/{field_name}/options/{value}")]
    with pytest.raises(HTTPException) as exc:
        update_opt(
            module_name="potentials",
            field_name="milestone",
            value=f"nope_{secrets.token_hex(3)}",
            payload={"label": "anything"},
            user=admin_user,
            db=db_handle,
        )
    assert exc.value.status_code == 404


def test_rename_empty_label_rejected_400(handlers, admin_user, db_handle):
    update_opt = handlers[("PATCH", "/modules/{module_name}/fields/{field_name}/options/{value}")]
    with pytest.raises(HTTPException) as exc:
        update_opt(
            module_name="potentials",
            field_name="milestone",
            value="won",
            payload={"label": "   "},
            user=admin_user,
            db=db_handle,
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# reorder_field_options
# ---------------------------------------------------------------------------


def test_delete_option_also_strips_orphan_label(handlers, admin_user, db_handle):
    """When admin removes a picklist value, its entry in
    ``field_config.option_labels`` should disappear too — otherwise the
    dict accretes dead labels over time (dev DB had 3 such orphans on
    potentials.milestone from probe traffic until alembic 0080 cleaned
    them up). This test re-creates the scenario: add + delete a value
    with a label, then assert option_labels is clean."""
    from hermes.crm.models.module_fields_db import ModuleFieldsDb

    add_opt = handlers[("POST", "/modules/{module_name}/fields/{field_name}/options")]
    delete_opt = handlers[("DELETE", "/modules/{module_name}/fields/{field_name}/options/{value}")]
    mod_id = int(db_handle.fetchone(
        "SELECT id FROM collections WHERE name='potentials'"
    )["id"])

    probe = f"orphan_probe_{secrets.token_hex(3)}"
    add_opt(
        module_name="potentials", field_name="milestone",
        payload={"value": probe, "label": f"测试-{probe}", "locale": "zh-CN"},
        user=admin_user, db=db_handle,
    )
    after_add = ModuleFieldsDb(db_handle).get_field_by_name(mod_id, "milestone")
    labels_after_add = (after_add.get("field_config") or {}).get("option_labels") or {}
    assert probe in labels_after_add, "label should have been written"

    delete_opt(
        module_name="potentials", field_name="milestone", value=probe,
        reason=None, user=admin_user, db=db_handle,
    )
    after_del = ModuleFieldsDb(db_handle).get_field_by_name(mod_id, "milestone")
    options_after_del = str(after_del.get("options") or "").split(",")
    labels_after_del = (after_del.get("field_config") or {}).get("option_labels") or {}
    assert probe not in options_after_del, "options CSV should drop the value"
    assert probe not in labels_after_del, (
        "field_config.option_labels should not retain the orphan label"
    )


def test_reorder_swap_two_options(handlers, admin_user, db_handle):
    """Swap initial_contact ↔ needs_analysis, then swap back so other
    tests aren't affected. Verifies the CSV is rewritten in the requested
    order and the response echoes the new options list."""
    from hermes.crm.models.module_fields_db import ModuleFieldsDb

    reorder = handlers[("PATCH", "/modules/{module_name}/fields/{field_name}/options/order")]
    mod_id = int(db_handle.fetchone(
        "SELECT id FROM collections WHERE name='potentials'"
    )["id"])
    before = ModuleFieldsDb(db_handle).get_field_by_name(mod_id, "milestone")
    original = str(before["options"]).split(",")
    assert original[0] == "initial_contact" and original[1] == "needs_analysis"

    swapped = [original[1], original[0]] + original[2:]
    r = reorder(
        module_name="potentials",
        field_name="milestone",
        payload={"order": swapped},
        user=admin_user,
        db=db_handle,
    )
    assert r["status"] == "reordered"
    assert r["options"] == swapped

    after = ModuleFieldsDb(db_handle).get_field_by_name(mod_id, "milestone")
    assert str(after["options"]).split(",") == swapped

    # restore
    reorder(
        module_name="potentials",
        field_name="milestone",
        payload={"order": original},
        user=admin_user,
        db=db_handle,
    )


def test_reorder_noop_when_same_order(handlers, admin_user, db_handle):
    from hermes.crm.models.module_fields_db import ModuleFieldsDb

    reorder = handlers[("PATCH", "/modules/{module_name}/fields/{field_name}/options/order")]
    mod_id = int(db_handle.fetchone(
        "SELECT id FROM collections WHERE name='potentials'"
    )["id"])
    current = str(ModuleFieldsDb(db_handle).get_field_by_name(mod_id, "milestone")["options"]).split(",")
    r = reorder(
        module_name="potentials",
        field_name="milestone",
        payload={"order": current},
        user=admin_user,
        db=db_handle,
    )
    assert r["status"] == "noop"


def test_reorder_rejects_set_diff(handlers, admin_user, db_handle):
    """Adding or dropping a value via reorder must 400 — reorder is
    permutation-only, no covert add/delete."""
    reorder = handlers[("PATCH", "/modules/{module_name}/fields/{field_name}/options/order")]
    # Drop one + add one bogus = both directions of the set check.
    with pytest.raises(HTTPException) as exc:
        reorder(
            module_name="potentials",
            field_name="milestone",
            payload={"order": [
                "needs_analysis", "proposal_sent", "negotiation",
                "closing", "won", "lost", "bogus_extra",
            ]},
            user=admin_user,
            db=db_handle,
        )
    assert exc.value.status_code == 400
    assert "permutation" in str(exc.value.detail).lower()


def test_reorder_rejects_duplicates(handlers, admin_user, db_handle):
    reorder = handlers[("PATCH", "/modules/{module_name}/fields/{field_name}/options/order")]
    with pytest.raises(HTTPException) as exc:
        reorder(
            module_name="potentials",
            field_name="milestone",
            payload={"order": ["won", "won"]},
            user=admin_user,
            db=db_handle,
        )
    assert exc.value.status_code == 400
    assert "duplicate" in str(exc.value.detail).lower()
