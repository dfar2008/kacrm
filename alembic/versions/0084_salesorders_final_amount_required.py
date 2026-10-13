"""Make ``salesorders.final_amount`` NOT NULL + skip zero-amount aggregation.

Why
---
``trg_salesorders_deal_aggregates`` (alembic 0077) is the single source
of truth for ``accounts.deal_amount`` / ``deal_count``. It SUMs
``salesorders.final_amount`` over orders whose ``status`` ∈ {confirmed,
delivered, completed}.

Two holes let through dirty data:

1. **NULL ``final_amount``** — agent path (``crm_write`` create) often
   ships a salesorder shell with only name + account + opportunity,
   stashing money downstream in receivables. The parent row's
   ``final_amount`` stayed NULL forever, so even after status was
   advanced the SUM contributed 0 — silent zero-aggregation bug.
2. **Zero ``final_amount``** — once we require NOT NULL, a stray
   ``final_amount = 0, status = 'confirmed'`` row would still inflate
   ``deal_count`` without contributing amount. By business rule a
   zero-money order is not a real deal.

This migration:

* Backfills the 5 existing NULL rows. Strategy: prefer
  ``SUM(receivables.dueamount)`` for the order (when present, that's
  the amount the agent already implied downstream); otherwise 0 for
  pure draft shells.
* ``ALTER COLUMN final_amount SET NOT NULL`` — schema-level guarantee
  every future order carries an amount; ``save.py`` adds a friendlier
  guard above this in the agent path so the error surfaces with a
  hint instead of a bare ``NotNullViolation``.
* Rewrites ``recompute_accounts_deal_aggregates`` to also skip
  ``final_amount <= 0`` from both SUM and COUNT — keeps the two columns
  consistent. Then recomputes every account so existing aggregates
  reflect the new rule (e.g. previously-NULL-now-0 orders that were
  status='confirmed' get dropped from the count).

Revision ID: 0084
Revises: 0083
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


_BACKFILL = """
UPDATE salesorders s
   SET final_amount   = COALESCE(rcv.total, 0),
       total_with_tax = COALESCE(s.total_with_tax, rcv.total, 0)
  FROM (
      SELECT salesorders_id, COALESCE(SUM(dueamount), 0)::numeric AS total
        FROM receivables
       GROUP BY salesorders_id
  ) rcv
 WHERE s.final_amount IS NULL
   AND s.salesorders_id = rcv.salesorders_id;

-- Pure draft shells with no receivables: pin to 0 so NOT NULL passes.
UPDATE salesorders
   SET final_amount = 0
 WHERE final_amount IS NULL;
"""


_RECOMPUTE_FN_V2 = """
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
             AND final_amount > 0
      ) agg
     WHERE a.accounts_id = p_accounts_id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_BACKFILL)
    op.execute(
        'ALTER TABLE salesorders ALTER COLUMN final_amount SET NOT NULL'
    )
    op.execute(_RECOMPUTE_FN_V2)
    # Recompute every account that has any salesorders so the new
    # "final_amount > 0" filter takes effect on existing aggregates.
    op.execute(
        """
        SELECT recompute_accounts_deal_aggregates(accounts_id)
          FROM (SELECT DISTINCT accounts_id FROM salesorders
                 WHERE accounts_id IS NOT NULL) AS s;
        """
    )


def downgrade() -> None:
    op.execute(
        'ALTER TABLE salesorders ALTER COLUMN final_amount DROP NOT NULL'
    )
    # Restore the pre-0084 function body (no final_amount > 0 filter).
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
    )
