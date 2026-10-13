"""Phase 3/3 — flip amount columns from JSONB to NUMERIC.

After 0078 created ``<col>_numeric NUMERIC(18,2)`` shadow columns and
back-filled them from the JSONB originals, this migration finalises
the swap:

  1. DROP every JSONB amount column (kacrm multi-currency legacy).
  2. RENAME each ``_numeric`` shadow back to the original name.
  3. DROP all four ``product_currency`` columns (categorical helper
     for the multi-currency envelope — unused without it).
  4. Recreate the 0077 ``recompute_accounts_deal_aggregates`` function
     to read NUMERIC ``final_amount`` directly (no more
     ``jsonb_typeof`` CASE).
  5. Flip any lingering ``module_fields.field_type`` of
     ``currency`` / ``currencyjson`` / ``currencyJson`` to ``number``
     so the SPA/admin shows the value as a plain number going forward.

Going forward, all 21 ex-JSONB columns are NUMERIC(18,2) — readers and
writers can drop their ``->>``  + ``jsonb_typeof`` defenses.

Revision ID: 0079
Revises: 0078
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


_AMOUNT_COLUMNS: list[tuple[str, str]] = [
    ("salesorders", "total_tax"),
    ("salesorders", "total_with_tax"),
    ("salesorders", "adjustment_amount"),
    ("salesorders", "final_amount"),
    ("salesorders", "invoice_amount"),
    ("salesorders", "payments_amount"),
    ("salesorders", "receivables_amount"),
    ("quotes", "total_tax"),
    ("quotes", "total_with_tax"),
    ("quotes", "adjustment_amount"),
    ("quotes", "final_amount"),
    ("salesorders_products", "tax_amount"),
    ("salesorders_products", "price"),
    ("salesorders_products", "subtotal"),
    ("salesorders_products", "total"),
    ("salesorders_products", "discount_price"),
    ("salesorders_products", "tax_after_price"),
    ("quotes_products", "tax_amount"),
    ("quotes_products", "price"),
    ("quotes_products", "subtotal"),
    ("quotes_products", "total"),
    ("quotes_products", "discount_price"),
    ("quotes_products", "tax_after_price"),
    ("payments", "payamount"),
    ("receivables", "dueamount"),
    ("receivables", "actualamount"),
    ("receivables", "balance"),
]

# product_currency lives on the 4 main money-bearing tables only;
# kacrm used it to pin the envelope's currency code. Multi-currency is
# gone, this column has nothing to do.
_PRODUCT_CURRENCY_TABLES = ["salesorders", "quotes", "payments", "receivables"]


# Rewritten ``recompute_accounts_deal_aggregates`` — reads NUMERIC
# ``final_amount`` directly. Replaces the jsonb_typeof CASE version
# introduced in 0077.
_RECOMPUTE_FN_NUMERIC = """
CREATE OR REPLACE FUNCTION recompute_accounts_deal_aggregates(
    p_accounts_id BIGINT
) RETURNS void AS $$
BEGIN
    IF p_accounts_id IS NULL OR p_accounts_id = 0 THEN
        RETURN;
    END IF;
    UPDATE accounts a
       SET deal_amount = COALESCE(agg.s, 0),
           deal_count  = COALESCE(agg.c, 0)
      FROM (
          SELECT COALESCE(SUM(final_amount), 0) AS s,
                 COUNT(*) AS c
            FROM salesorders
           WHERE accounts_id = p_accounts_id
             AND (deleted IS NULL OR deleted = FALSE)
             AND status IN ('confirmed', 'delivered', 'completed')
      ) agg
     WHERE a.accounts_id = p_accounts_id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # 1. DROP each JSONB original then RENAME shadow into its place.
    for table, col in _AMOUNT_COLUMNS:
        # CASCADE handles any view / index hanging off the JSONB column.
        # We control the only known dependent (0077 trigger function),
        # which we rebuild below — and trigger functions are NOT bound
        # to column types until executed, so CASCADE is just defensive.
        op.execute(f'ALTER TABLE {table} DROP COLUMN {col} CASCADE')
        op.execute(
            f'ALTER TABLE {table} RENAME COLUMN {col}_numeric TO {col}'
        )

    # 2. DROP product_currency columns. CASCADE in case anything
    # downstream referenced them (nothing in code does, per 2026-05-26
    # sweep, but defensive).
    for table in _PRODUCT_CURRENCY_TABLES:
        op.execute(
            f'ALTER TABLE {table} DROP COLUMN IF EXISTS product_currency CASCADE'
        )

    # 3. Recreate the deal-aggregates function for NUMERIC final_amount.
    # CASCADE in step 1 may have torn down the previous version; this
    # CREATE OR REPLACE puts it back with the simpler SQL.
    op.execute(_RECOMPUTE_FN_NUMERIC)

    # 4. Flip lingering currency / currencyjson field_type to number.
    # accounts.deal_amount and accounts.credit_limit are the only
    # current rows with field_type='currency'; the columns themselves
    # have been NUMERIC since 0004, so this is purely a UI-side relabel.
    op.execute(
        """
        UPDATE module_fields
           SET field_type = 'number'
         WHERE field_type IN ('currency', 'currencyjson', 'currencyJson')
        """
    )

    # 5. Recreate the dashboard views (0059) — step 1's DROP CASCADE
    # tore them down. New definitions just SELECT the NUMERIC columns
    # directly; no more ``_jsonb_amount`` CASE wrapper. Other parts of
    # the view shape (renames, WHERE clauses) are byte-identical to 0059.
    op.execute("DROP VIEW IF EXISTS v_dashboard_payments CASCADE;")
    op.execute("DROP VIEW IF EXISTS v_dashboard_receivables CASCADE;")
    op.execute("DROP VIEW IF EXISTS v_dashboard_quotes CASCADE;")
    op.execute("DROP VIEW IF EXISTS v_dashboard_orders CASCADE;")
    op.execute(
        """
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
            final_amount       AS amount,
            total_with_tax     AS amount_with_tax,
            receivables_amount AS receivables_amount_v,
            payments_amount    AS payments_amount_v,
            created_at,
            updated_at
        FROM salesorders
        WHERE deleted = FALSE;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_quotes AS
        SELECT
            quotes_id      AS id,
            quotes_name    AS name,
            owner_by       AS owner_user_id,
            department_id,
            accounts_id,
            contacts_id,
            potentials_id,
            status,
            quotedate,
            validity_end,
            total_quantity,
            final_amount   AS amount,
            total_with_tax AS amount_with_tax,
            created_at,
            updated_at
        FROM quotes
        WHERE deleted = FALSE;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_receivables AS
        SELECT
            receivables_id    AS id,
            receivables_name  AS name,
            owner_by          AS owner_user_id,
            department_id,
            accounts_id,
            contacts_id,
            salesorders_id,
            receivablesstatus AS status,
            duedate,
            actualdate,
            dueamount    AS due_amount,
            actualamount AS actual_amount,
            balance      AS balance,
            created_at,
            updated_at
        FROM receivables
        WHERE deleted = FALSE;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_dashboard_payments AS
        SELECT
            payments_id   AS id,
            payments_name AS name,
            owner_by      AS owner_user_id,
            department_id,
            receivables_id,
            bankaccount_id,
            paydate,
            remark,
            payamount AS amount,
            created_at,
            updated_at
        FROM payments
        WHERE deleted = FALSE;
        """
    )


def downgrade() -> None:
    # This is a forward-only migration in practice — recovering the
    # JSONB envelope shape from NUMERIC requires guessing a currency
    # code we never stored. The downgrade is provided as a courtesy
    # for local dev rollback only, with all values wrapped as
    # ``{"CNY": N}`` (the historically dominant shape).
    for table, col in _AMOUNT_COLUMNS:
        op.execute(
            f'ALTER TABLE {table} RENAME COLUMN {col} TO {col}_numeric'
        )
        op.execute(
            f'ALTER TABLE {table} ADD COLUMN {col} JSONB'
        )
        op.execute(
            f"""
            UPDATE {table}
               SET {col} = jsonb_build_object('CNY', {col}_numeric)
             WHERE {col}_numeric IS NOT NULL
            """
        )

    for table in _PRODUCT_CURRENCY_TABLES:
        op.execute(f'ALTER TABLE {table} ADD COLUMN product_currency JSONB')

    # Restore the 0077 trigger function's jsonb_typeof CASE form.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION recompute_accounts_deal_aggregates(
            p_accounts_id BIGINT
        ) RETURNS void AS $$
        BEGIN
            IF p_accounts_id IS NULL OR p_accounts_id = 0 THEN
                RETURN;
            END IF;
            UPDATE accounts a
               SET deal_amount = COALESCE(agg.s, 0),
                   deal_count  = COALESCE(agg.c, 0)
              FROM (
                  SELECT
                      COALESCE(SUM(
                          CASE jsonb_typeof(final_amount)
                              WHEN 'object' THEN
                                  NULLIF(final_amount->>'original_amount', '')::numeric
                              WHEN 'number' THEN
                                  (final_amount #>> '{}')::numeric
                              ELSE 0
                          END
                      ), 0) AS s,
                      COUNT(*) AS c
                    FROM salesorders
                   WHERE accounts_id = p_accounts_id
                     AND (deleted IS NULL OR deleted = FALSE)
                     AND status IN ('confirmed', 'delivered', 'completed')
              ) agg
             WHERE a.accounts_id = p_accounts_id;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        UPDATE module_fields
           SET field_type = 'currency'
         WHERE field_name IN ('deal_amount', 'credit_limit')
           AND module_id IN (SELECT id FROM collections WHERE name = 'accounts')
        """
    )
