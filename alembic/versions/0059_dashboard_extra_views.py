"""Dashboards source views — products / orders / quotes / receivables / payments.

These five tables hold monetary values as JSONB (e.g. ``{"CNY": 400000}``);
the runtime auto-rebuild can't cast those, so we hand-write the views here
and the corresponding YAMLs omit ``module_table`` so they are skipped by
``rebuild_dashboard_views``.

Revision ID: 0059
Revises: 0058
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op


revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


# CNY-first jsonb-to-numeric extractor. Handles three shapes observed in
# the wild:
#   1) ``{"CNY": 200000}``                     → 200000
#   2) bare scalar ``200000``                  → 200000
#   3) any other shape (``{"USD": 200000}``,
#      missing key, malformed)                 → NULL (not a SQL error)
#
# The earlier draft used ``NULLIF(col::text, 'null')::numeric`` as the
# fallback, which works for scalar JSON but raises ``invalid input syntax``
# for object JSON like ``{"USD":200000}`` — breaking the whole view query
# the moment a row has a non-CNY currency. ``jsonb_typeof = 'number'`` is
# the safe gate for the scalar branch.
def _jsonb_amount(col: str) -> str:
    return (
        f"COALESCE(("
        f"  CASE WHEN jsonb_typeof({col}) = 'object'"
        f"       THEN ({col}->>'CNY')::numeric"
        f"       WHEN jsonb_typeof({col}) = 'number'"
        f"       THEN ({col}::text)::numeric"
        f"       ELSE NULL"
        f"  END"
        f"), NULL)"
    )


def upgrade() -> None:
    for v in (
        "v_dashboard_payments",
        "v_dashboard_receivables",
        "v_dashboard_quotes",
        "v_dashboard_orders",
        "v_dashboard_products",
    ):
        op.execute(f"DROP VIEW IF EXISTS {v} CASCADE;")

    # -- product (products) --------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_products AS
        SELECT
            products_id     AS id,
            products_name   AS name,
            owner_by        AS owner_user_id,
            department_id,
            status,
            catalog,
            productno,
            stock,
            safe_stock,
            price,
            cost_price,
            description,
            created_at,
            updated_at
        FROM products
        WHERE deleted = FALSE;
        """
    )

    # -- order (salesorders) -------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE VIEW v_dashboard_orders AS
        SELECT
            salesorders_id     AS id,
            salesorders_name   AS name,
            owner_by           AS owner_user_id,
            department_id,
            accounts_id,
            contacts_id,
            potentials_id,
            status,
            ordertype,
            payment_status,
            invoicestatus,
            orderdate,
            total_quantity,
            { _jsonb_amount("final_amount") }      AS amount,
            { _jsonb_amount("total_with_tax") }    AS amount_with_tax,
            { _jsonb_amount("receivables_amount") } AS receivables_amount_v,
            { _jsonb_amount("payments_amount") }   AS payments_amount_v,
            created_at,
            updated_at
        FROM salesorders
        WHERE deleted = FALSE;
        """
    )

    # -- quote (quotes) ------------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE VIEW v_dashboard_quotes AS
        SELECT
            quotes_id     AS id,
            quotes_name   AS name,
            owner_by      AS owner_user_id,
            department_id,
            accounts_id,
            contacts_id,
            potentials_id,
            status,
            quotedate,
            validity_end,
            total_quantity,
            { _jsonb_amount("final_amount") }   AS amount,
            { _jsonb_amount("total_with_tax") } AS amount_with_tax,
            created_at,
            updated_at
        FROM quotes
        WHERE deleted = FALSE;
        """
    )

    # -- receivable (receivables) -------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE VIEW v_dashboard_receivables AS
        SELECT
            receivables_id     AS id,
            receivables_name   AS name,
            owner_by           AS owner_user_id,
            department_id,
            accounts_id,
            contacts_id,
            salesorders_id,
            receivablesstatus  AS status,
            duedate,
            actualdate,
            { _jsonb_amount("dueamount") }    AS due_amount,
            { _jsonb_amount("actualamount") } AS actual_amount,
            { _jsonb_amount("balance") }      AS balance,
            created_at,
            updated_at
        FROM receivables
        WHERE deleted = FALSE;
        """
    )

    # -- payment (payments) -------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE VIEW v_dashboard_payments AS
        SELECT
            payments_id     AS id,
            payments_name   AS name,
            owner_by        AS owner_user_id,
            department_id,
            receivables_id,
            bankaccount_id,
            paydate,
            remark,
            { _jsonb_amount("payamount") } AS amount,
            created_at,
            updated_at
        FROM payments
        WHERE deleted = FALSE;
        """
    )


def downgrade() -> None:
    for v in (
        "v_dashboard_payments",
        "v_dashboard_receivables",
        "v_dashboard_quotes",
        "v_dashboard_orders",
        "v_dashboard_products",
    ):
        op.execute(f"DROP VIEW IF EXISTS {v};")
