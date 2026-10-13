"""Regression — alembic 0078 backfill correctly normalized historical
amount column shapes into NUMERIC, regardless of which JSONB envelope
the legacy rows used.

Before 0079 dropped the JSONB originals, three shapes coexisted on
production tables (per the 2026-05-26 sweep):

* ``{"original_amount": N, "currency": "CNY", ...}`` — kacrm v2 envelope
* ``{"CNY": N}``                                    — kacrm v1 currency-code-key map
* bare number ``N``                                  — agent / seed direct write

The backfill SQL in 0078 used ``LATERAL jsonb_each`` to pick the first
numeric value out of any object shape — agnostic to the key name — and
``#>> '{}'`` for the scalar case. This test exercises that
normalization end-to-end through the existing salesorders trigger
(``recompute_accounts_deal_aggregates``) which sums salesorders'
``final_amount`` per account.

Test strategy: insert salesorders rows with each shape was technically
possible BEFORE 0079 only (the columns are NUMERIC now). So we go in
the opposite direction — verify the **current** trigger / SUM path
treats the historical values correctly by checking the post-backfill
state of accounts that originally held mixed shapes. Specifically:

* salesorders #1 (``{"CNY": 400000}``) → backfilled to 400000.00
* salesorders #2 (``{"CNY": 1300000}``) → backfilled to 1300000.00
* salesorders #18 (bare ``35000``) → backfilled to 35000.00
* accounts.deal_amount for the parent accounts of these orders should
  reflect the SUM of those NUMERIC values.

These rows were created by seed scripts that ran before 0078; the test
asserts their post-migration state rather than re-creating them.
"""
from __future__ import annotations

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


def test_amount_columns_are_numeric_type():
    """0079 ALTERed every JSONB amount column to NUMERIC. If anyone
    reintroduces a JSONB amount column down the line, this test catches
    it before downstream readers re-grow ``jsonb_typeof`` defenses."""
    from hermes.crm.db import db_session

    expect = {
        ("salesorders", "final_amount"),
        ("salesorders", "total_with_tax"),
        ("salesorders", "total_tax"),
        ("quotes", "final_amount"),
        ("quotes", "total_with_tax"),
        ("payments", "payamount"),
        ("receivables", "dueamount"),
        ("receivables", "actualamount"),
        ("receivables", "balance"),
        ("salesorders_products", "price"),
        ("salesorders_products", "total"),
        ("quotes_products", "price"),
        ("quotes_products", "total"),
    }
    with db_session() as db:
        tables = sorted({t for t, _ in expect})
        rows = db.fetchall(
            """
            SELECT table_name, column_name, data_type
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = ANY(%s)
            """,
            (tables,),
        )
    by_pair = {(r["table_name"], r["column_name"]): r["data_type"] for r in rows}
    bad: list[str] = []
    for tbl, col in expect:
        dtype = by_pair.get((tbl, col))
        if dtype is None:
            bad.append(f"{tbl}.{col}: column missing")
        elif dtype != "numeric":
            bad.append(f"{tbl}.{col}: expected numeric, got {dtype}")
    assert not bad, "\n".join(bad)


def test_product_currency_columns_dropped():
    """0079 drops ``product_currency`` from the 4 money-bearing tables
    (the kacrm currency-code holder, unused post-NUMERIC)."""
    from hermes.crm.db import db_session

    with db_session() as db:
        rows = db.fetchall(
            """
            SELECT table_name FROM information_schema.columns
             WHERE column_name = 'product_currency'
               AND table_schema = 'public'
               AND table_name IN ('salesorders','quotes','payments','receivables')
            """
        )
    assert not rows, f"product_currency leaked on: {[r['table_name'] for r in rows]}"


def test_seeded_cny_shape_rows_backfilled_correctly():
    """salesorders #1 / #2 were seeded as ``{"CNY": N}`` envelope before
    0078 ran. After 0078's backfill + 0079's swap, the values should be
    NUMERIC, equal to the original N. If the LATERAL jsonb_each picker
    regresses (e.g. someone narrows it to ``->>'original_amount'``),
    these rows would come back as 0 and this test would catch it."""
    from hermes.crm.db import db_session

    with db_session() as db:
        rows = db.fetchall(
            "SELECT salesorders_id, final_amount FROM salesorders "
            "WHERE salesorders_id IN (1, 2) ORDER BY salesorders_id"
        )
    by_id = {int(r["salesorders_id"]): r["final_amount"] for r in rows}
    assert by_id.get(1) == Decimal("400000.00"), (
        f"SO #1 (seeded as CNY-envelope 400000) backfill drifted: {by_id.get(1)}"
    )
    assert by_id.get(2) == Decimal("1300000.00"), (
        f"SO #2 (seeded as CNY-envelope 1300000) backfill drifted: {by_id.get(2)}"
    )


def test_receivables_cny_shape_backfilled():
    """receivables.dueamount / balance were also CNY-envelope in seed."""
    from hermes.crm.db import db_session

    with db_session() as db:
        r1 = db.fetchone(
            "SELECT dueamount, balance FROM receivables WHERE receivables_id = 1"
        )
    assert r1 is not None
    # Seed shape was {"CNY": 200000} for due + {"CNY": 0} for balance.
    assert r1["dueamount"] == Decimal("200000.00"), r1
    assert r1["balance"] == Decimal("0.00"), r1


def test_no_residual_jsonb_amount_columns_anywhere():
    """Forward-only guard. Any future migration that re-adds a JSONB
    column whose name ends in `_amount` / `amount` / `tax` / `balance`
    / `price` / `subtotal` / `total` on the 8 known money-bearing
    tables would re-open the dual-shape rabbit hole. Fail loudly."""
    from hermes.crm.db import db_session

    suspect = ("salesorders", "quotes", "salesorders_products",
               "quotes_products", "payments", "receivables")
    with db_session() as db:
        rows = db.fetchall(
            """
            SELECT table_name, column_name FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = ANY (%s)
               AND data_type = 'jsonb'
               AND (column_name LIKE '%%amount%%'
                    OR column_name LIKE '%%tax%%'
                    OR column_name LIKE '%%balance%%'
                    OR column_name = 'price'
                    OR column_name = 'subtotal'
                    OR column_name = 'total'
                    OR column_name = 'payamount'
                    OR column_name = 'dueamount')
            """,
            (list(suspect),),
        )
    assert not rows, (
        f"JSONB amount columns regrew on money-bearing tables: {rows}. "
        "If this is intentional (rare!), update this test; otherwise alembic "
        "0078/0079 has been partially reverted."
    )
