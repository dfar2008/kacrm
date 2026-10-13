"""Regression — accounts.deal_amount / deal_count are now maintained by
a PG trigger on salesorders (alembic 0077, simplified by 0079).

After alembic 0079 (2026-05-26) the salesorders amount columns are
NUMERIC(18,2). The trigger function reads ``final_amount`` directly —
no more JSONB / dual-shape special-casing.

Aggregation rule:
``deal_count`` = COUNT(salesorders WHERE status ∈ terminal AND not deleted)
``deal_amount`` = SUM(final_amount) under the same filter.
"""
from __future__ import annotations

import secrets
import sys
from decimal import Decimal
from pathlib import Path

import pytest

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


@pytest.fixture
def fresh_account():
    """Seed an isolated empty account so other accounts' aggregates
    aren't disturbed by the test's salesorder churn."""
    from hermes.crm.db import db_session

    with db_session() as db:
        admin_id = int(
            db.fetchone("SELECT id FROM users WHERE username='admin'")["id"]
        )
        name = f"trigger-test-{secrets.token_hex(3)}"
        row = db.fetchone(
            """
            INSERT INTO accounts (accounts_name, owner_by, created_by, updated_by)
            VALUES (%s, %s, %s, %s) RETURNING accounts_id
            """,
            (name, admin_id, admin_id, admin_id),
        )
        aid = int(row["accounts_id"])
    yield aid, admin_id
    with db_session() as db:
        db.execute("DELETE FROM salesorders WHERE accounts_id = %s", (aid,))
        db.execute("DELETE FROM accounts WHERE accounts_id = %s", (aid,))


def _seed_so(db, accounts_id, admin_id, status, amount):
    row = db.fetchone(
        """
        INSERT INTO salesorders (salesorders_name, accounts_id, status, final_amount,
                                 owner_by, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING salesorders_id
        """,
        (
            f"so-{secrets.token_hex(3)}", accounts_id, status, amount,
            admin_id, admin_id, admin_id,
        ),
    )
    return int(row["salesorders_id"])


def _deal_stats(db, aid):
    r = db.fetchone(
        "SELECT deal_amount, deal_count FROM accounts WHERE accounts_id=%s",
        (aid,),
    )
    return r["deal_amount"], int(r["deal_count"])


def test_insert_confirmed_order_increments_aggregates(fresh_account):
    from hermes.crm.db import db_session
    aid, admin_id = fresh_account

    with db_session() as db:
        assert _deal_stats(db, aid) == (Decimal("0"), 0)
        _seed_so(db, aid, admin_id, "confirmed", 35000)
        assert _deal_stats(db, aid) == (Decimal("35000"), 1)
        _seed_so(db, aid, admin_id, "completed", 12500)
        assert _deal_stats(db, aid) == (Decimal("47500"), 2)


def test_non_terminal_status_does_not_count(fresh_account):
    """Created / cancelled don't contribute. Confirmed does."""
    from hermes.crm.db import db_session
    aid, admin_id = fresh_account

    with db_session() as db:
        _seed_so(db, aid, admin_id, "created", 99999)
        _seed_so(db, aid, admin_id, "cancelled", 99999)
        assert _deal_stats(db, aid) == (Decimal("0"), 0)
        _seed_so(db, aid, admin_id, "confirmed", 5000)
        assert _deal_stats(db, aid) == (Decimal("5000"), 1)


def test_status_flip_to_cancelled_rolls_back(fresh_account):
    from hermes.crm.db import db_session
    aid, admin_id = fresh_account

    with db_session() as db:
        sid = _seed_so(db, aid, admin_id, "confirmed", 8000)
        assert _deal_stats(db, aid) == (Decimal("8000"), 1)
        db.execute(
            "UPDATE salesorders SET status='cancelled' WHERE salesorders_id=%s",
            (sid,),
        )
        assert _deal_stats(db, aid) == (Decimal("0"), 0)


def test_reassign_accounts_id_recomputes_both(fresh_account):
    """Moving an order from account A to account B updates both ends."""
    from hermes.crm.db import db_session
    aid_a, admin_id = fresh_account

    with db_session() as db:
        bname = f"trigger-test-b-{secrets.token_hex(3)}"
        aid_b = int(db.fetchone(
            """
            INSERT INTO accounts (accounts_name, owner_by, created_by, updated_by)
            VALUES (%s, %s, %s, %s) RETURNING accounts_id
            """,
            (bname, admin_id, admin_id, admin_id),
        )["accounts_id"])
    try:
        with db_session() as db:
            sid = _seed_so(db, aid_a, admin_id, "confirmed", 4000)
            assert _deal_stats(db, aid_a) == (Decimal("4000"), 1)
            assert _deal_stats(db, aid_b) == (Decimal("0"), 0)

            db.execute(
                "UPDATE salesorders SET accounts_id=%s WHERE salesorders_id=%s",
                (aid_b, sid),
            )
            assert _deal_stats(db, aid_a) == (Decimal("0"), 0)
            assert _deal_stats(db, aid_b) == (Decimal("4000"), 1)
    finally:
        with db_session() as db:
            db.execute("DELETE FROM salesorders WHERE accounts_id IN (%s, %s)", (aid_a, aid_b))
            db.execute("DELETE FROM accounts WHERE accounts_id = %s", (aid_b,))


def test_soft_delete_decrements(fresh_account):
    from hermes.crm.db import db_session
    aid, admin_id = fresh_account

    with db_session() as db:
        sid = _seed_so(db, aid, admin_id, "confirmed", 6000)
        assert _deal_stats(db, aid) == (Decimal("6000"), 1)
        db.execute(
            "UPDATE salesorders SET deleted=TRUE WHERE salesorders_id=%s",
            (sid,),
        )
        assert _deal_stats(db, aid) == (Decimal("0"), 0)


def test_string_final_amount_also_aggregates(fresh_account):
    """psycopg can be lazy and pass numbers as strings depending on
    driver/encoder. NUMERIC columns coerce strings, so this still
    aggregates correctly."""
    from hermes.crm.db import db_session
    aid, admin_id = fresh_account

    with db_session() as db:
        db.execute(
            """
            INSERT INTO salesorders (salesorders_name, accounts_id, status,
                                     final_amount, owner_by, created_by, updated_by)
            VALUES (%s, %s, 'confirmed', %s, %s, %s, %s)
            """,
            (
                f"so-str-{secrets.token_hex(3)}", aid, "35000",
                admin_id, admin_id, admin_id,
            ),
        )
        assert _deal_stats(db, aid) == (Decimal("35000"), 1)
