"""Phase 1/3 of migrating amount columns JSONB → NUMERIC.

History
-------
21 ``*_amount`` / ``total_*`` / ``price`` / ``subtotal`` / ``total`` /
``dueamount`` / ``balance`` / ``payamount`` columns across 8 record
tables were declared ``JSONB`` to inherit kacrm's multi-currency
envelope ``{"original_amount": N, "currency": "CNY", ...}``. hermes
never used multi-currency — production writers (agent, line-item
rollups, seed) ended up writing bare numbers ``35000`` into the same
columns, so every reader has to ``CASE jsonb_typeof(...)`` to handle
both shapes. Drift was distributed across 5+ code paths and a recent
trigger (0077) had to special-case it too. See
2026-05-26 thread for the full diagnosis.

This Migration (Phase 1)
------------------------
Adds a ``<col>_numeric NUMERIC(18,2)`` shadow column next to every
JSONB amount column and back-fills it from either shape. The original
JSONB columns are left in place so the running deploy keeps working;
Phase 2 sweeps every reader/writer to the shadow columns, then Phase 3
(``0079_amount_jsonb_drop_legacy``) drops the JSONB originals and
renames shadows back.

Backfill rule (per column):
  * ``jsonb_typeof(col) = 'object'`` → first non-null numeric value from
    the object. Real data uses two shapes interchangeably:
    ``{"original_amount": N}`` (kacrm v2 envelope) AND
    ``{"CNY": N}`` (kacrm v1 single-currency map). We don't care which
    key — there's only ever one number in the dict.
  * ``jsonb_typeof(col) = 'number'`` → ``(col#>>'{}')::numeric``
  * anything else (NULL / empty string / unexpected) → NULL

Revision ID: 0078
Revises: 0077
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


# (table, column) pairs covering every JSONB amount column declared in
# alembic 0011_crm_modules.py. Currency-marker column ``product_currency``
# (4 tables) is NOT in this list — it's a categorical label, not an
# amount; handled in 0079 (dropped wholesale).
_AMOUNT_COLUMNS: list[tuple[str, str]] = [
    # salesorders parent
    ("salesorders", "total_tax"),
    ("salesorders", "total_with_tax"),
    ("salesorders", "adjustment_amount"),
    ("salesorders", "final_amount"),
    ("salesorders", "invoice_amount"),
    ("salesorders", "payments_amount"),
    ("salesorders", "receivables_amount"),
    # quotes parent
    ("quotes", "total_tax"),
    ("quotes", "total_with_tax"),
    ("quotes", "adjustment_amount"),
    ("quotes", "final_amount"),
    # salesorders_products line items
    ("salesorders_products", "tax_amount"),
    ("salesorders_products", "price"),
    ("salesorders_products", "subtotal"),
    ("salesorders_products", "total"),
    ("salesorders_products", "discount_price"),
    ("salesorders_products", "tax_after_price"),
    # quotes_products line items
    ("quotes_products", "tax_amount"),
    ("quotes_products", "price"),
    ("quotes_products", "subtotal"),
    ("quotes_products", "total"),
    ("quotes_products", "discount_price"),
    ("quotes_products", "tax_after_price"),
    # payments
    ("payments", "payamount"),
    # receivables
    ("receivables", "dueamount"),
    ("receivables", "actualamount"),
    ("receivables", "balance"),
]


def _backfill_sql(table: str, col: str) -> str:
    shadow = f"{col}_numeric"
    return f"""
        UPDATE {table}
           SET {shadow} = CASE jsonb_typeof({col})
               WHEN 'object' THEN (
                   -- Pull the first numeric value, regardless of key
                   -- (handles both 'original_amount' and currency-code
                   -- 'CNY' shapes). LATERAL ensures one scan per row.
                   SELECT (v #>> '{{}}')::numeric
                     FROM jsonb_each({col}) AS kv(k, v)
                    WHERE jsonb_typeof(v) = 'number'
                    LIMIT 1
               )
               WHEN 'number' THEN ({col} #>> '{{}}')::numeric
               ELSE NULL
           END
         WHERE {col} IS NOT NULL
    """


def upgrade() -> None:
    for table, col in _AMOUNT_COLUMNS:
        shadow = f"{col}_numeric"
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            f"{shadow} NUMERIC(18,2)"
        )
        op.execute(_backfill_sql(table, col))


def downgrade() -> None:
    # Drop shadow columns only; original JSONB columns and their data
    # were never touched by Phase 1.
    for table, col in _AMOUNT_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}_numeric"
        )
