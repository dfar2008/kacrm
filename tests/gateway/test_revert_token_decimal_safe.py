"""Regression — gateway must not blow up on Decimal/date/UUID inside
revert_token (or any other JSONB payload it persists).

Root cause: ``hermes/action_gateway/dispatcher.py::_json_or_null`` used
``json.dumps(val)`` with no ``default=``. The ``crm.write`` executor
snapshots ``old_values`` straight from ``db.fetchone(...)`` into the
revert token, which carries Python ``Decimal`` for ``NUMERIC`` columns
(and ``datetime``/``date``/``UUID`` for their column kinds). Any agent
update touching a column whose old value was non-NULL Decimal made the
gateway txn roll back — the whole write disappeared and the agent saw
a generic failure (e.g. ``accounts.deal_amount``).

Fix: ``_json_default`` covers Decimal/date/datetime/UUID, and the
``crm_write`` executor normalises ``old_values`` through ``_normalize``
before stuffing it into the revert token.
"""
from __future__ import annotations

import json
import secrets
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Unit tests — no DB
# ---------------------------------------------------------------------------


def test_json_or_null_accepts_decimal():
    from hermes.action_gateway.dispatcher import _json_or_null

    out = _json_or_null({"deal_amount": Decimal("35000.00")})
    assert out is not None
    assert json.loads(out) == {"deal_amount": "35000.00"}


def test_json_or_null_accepts_datetime_date_uuid():
    from hermes.action_gateway.dispatcher import _json_or_null

    payload = {
        "ts": datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc),
        "d": date(2026, 5, 25),
        "u": uuid4(),
    }
    decoded = json.loads(_json_or_null(payload))  # type: ignore[arg-type]
    assert decoded["ts"].startswith("2026-05-25T09:30")
    assert decoded["d"] == "2026-05-25"
    assert isinstance(decoded["u"], str)


def test_json_or_null_preserves_unicode_without_escaping():
    """``ensure_ascii=False`` keeps human-readable Chinese in
    ``gateway_actions.params`` so DB introspection is legible."""
    from hermes.action_gateway.dispatcher import _json_or_null

    out = _json_or_null({"summary": "杭州司目科技"})
    assert out is not None
    assert "杭州司目科技" in out


def test_json_or_null_none_returns_none():
    from hermes.action_gateway.dispatcher import _json_or_null

    assert _json_or_null(None) is None


def test_json_default_raises_on_truly_unknown_type():
    """An unknown type must still raise — silently coercing arbitrary
    objects via ``str()`` would let garbage land in JSONB columns."""
    from hermes.action_gateway.dispatcher import _json_default

    class Opaque:
        pass

    with pytest.raises(TypeError):
        _json_default(Opaque())


# ---------------------------------------------------------------------------
# Integration test — full gateway round trip on accounts.deal_amount
# ---------------------------------------------------------------------------


def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _crm_reachable(), reason="hermes_crm_dev not reachable")
def test_gateway_update_with_decimal_old_value_persists_revert_token():
    """Before the fix this rolled back the txn and the agent saw a
    generic failure. After the fix the write executes and revert_token
    round-trips through ``::jsonb``."""
    import hermes_cloud.app.gateway  # noqa: F401 — register crm.write executor
    from hermes.action_gateway.dispatcher import submit
    from hermes.crm.db import db_session
    from hermes.trust.engine import set_level

    with db_session() as db:
        admin = db.fetchone("SELECT id FROM users WHERE username='admin' LIMIT 1")
        mod = db.fetchone(
            "SELECT id FROM collections WHERE name='accounts' LIMIT 1"
        )
        prof = db.fetchone("SELECT id FROM profiles LIMIT 1")
    assert admin and mod and prof
    admin_id = int(admin["id"])
    mod_id = int(mod["id"])
    profile = str(prof["id"])

    agent = f"dec_{secrets.token_hex(3)}"
    set_level(profile, agent, "crm.write", 5)

    # Seed an accounts row with a NON-NULL deal_amount so old_values
    # carries a real Decimal through the executor.
    name = f"GW-Decimal-{secrets.token_hex(3)}"
    with db_session() as db:
        rid = db.fetchone(
            'INSERT INTO accounts (accounts_name, owner_by, deal_amount, '
            'deal_count, created_by, updated_by) '
            'VALUES (%s, %s, %s, %s, %s, %s) RETURNING accounts_id',
            (name, admin_id, Decimal("0.00"), 0, admin_id, admin_id),
        )["accounts_id"]

    action_id = None
    try:
        result = submit(
            profile_id=profile,
            agent_name=agent,
            kind="crm.write",
            params={
                "module_id": mod_id,
                "op": "update",
                "record_id": int(rid),
                "data": {"deal_amount": 35000, "deal_count": 1},
            },
            user_id=admin_id,
        )
        action_id = str(result["id"])

        assert result["state"] == "executed", (
            f"write must execute (was {result['state']}). "
            "If 'pending_approval' the fix didn't take effect because "
            "the executor never ran; if the previous bug regressed the "
            "txn rolled back and we wouldn't even get here."
        )
        tok = result["revert_token"]
        assert tok["op"] == "restore"
        # The key fix: old_values's Decimal must survive normalization
        # as a JSON-safe string.
        assert tok["old_values"]["deal_amount"] == "0.00"

        with db_session() as db:
            row = db.fetchone(
                "SELECT deal_amount, deal_count FROM accounts "
                "WHERE accounts_id = %s",
                (int(rid),),
            )
            persisted = db.fetchone(
                "SELECT revert_token FROM gateway_actions WHERE id = %s",
                (action_id,),
            )
        assert row["deal_amount"] == Decimal("35000.00")
        assert int(row["deal_count"]) == 1
        # revert_token actually landed in JSONB (psycopg returns dict).
        assert persisted["revert_token"]["old_values"]["deal_amount"] == "0.00"
    finally:
        with db_session() as db:
            if action_id:
                db.execute(
                    "DELETE FROM trust_decisions WHERE action_id = %s",
                    (action_id,),
                )
                db.execute(
                    "DELETE FROM gateway_actions WHERE id = %s", (action_id,)
                )
            db.execute(
                "DELETE FROM accounts WHERE accounts_id = %s", (int(rid),)
            )
            db.execute(
                "DELETE FROM trust_levels WHERE profile_id=%s AND agent_name=%s",
                (profile, agent),
            )
