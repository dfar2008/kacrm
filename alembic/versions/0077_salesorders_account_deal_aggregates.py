"""Maintain accounts.deal_amount / deal_count from salesorders via trigger.

Story
-----
Both columns were seeded as ``readonly=True, is_summary=True`` (since
2026-04-18 record-insights doc) but no trigger ever maintained them.
Agents that wanted to record a closed deal had to write the aggregates
manually — and the SPA / seed paths had no way to keep them in sync.
Result: ``accounts.deal_count`` / ``deal_amount`` stayed at the schema
default of 0 forever unless someone touched them by hand.

Trigger source
--------------
We tie aggregation to ``salesorders``, NOT ``potentials.milestone='won'``.
Reasoning:

* ``salesorders`` is the real contract; ``potentials.milestone='won'``
  is just "sales-stage won" — admins frequently flip a potential to
  won early as a sales-team morale marker, then later the contract
  falls through. Using salesorders means cancelled orders auto-roll
  back without admin re-edits.
* ``salesorders.final_amount`` (JSONB ``original_amount``) is the
  signed contract amount; ``potentials.amount`` is a forecast.
* salesorders has ``status`` with explicit terminal values
  (confirmed / delivered / completed are "real"; created / cancelled
  are not). potentials' won is implicit.

Aggregation rule
----------------
``deal_amount`` = SUM of ``final_amount->>'original_amount'`` over
non-deleted salesorders whose status ∈
{confirmed, delivered, completed}.
``deal_count`` = COUNT under the same filter.

A reassignment (UPDATE OF accounts_id) recomputes both the old and new
parent's aggregates. Hard DELETE and soft-delete (UPDATE SET deleted=true)
both flow through the same recompute.

Revision ID: 0077
Revises: 0076
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


_RECOMPUTE_FN = """
CREATE OR REPLACE FUNCTION recompute_accounts_deal_aggregates(
    p_accounts_id BIGINT
) RETURNS void AS $$
BEGIN
    IF p_accounts_id IS NULL OR p_accounts_id = 0 THEN
        RETURN;
    END IF;
    -- final_amount 列名义 JSONB（kacrm 多币种 legacy），但实际两种形态
    -- 都在用：dict ``{"original_amount": 35000}``（标准）和裸数字
    -- ``35000``（agent / 旧 seed 直写）。``->>`` 对裸数字返回 NULL，
    -- 会让汇总错算成 0。jsonb_typeof 兼容两种 shape。
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


_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION trg_salesorders_recompute_account_deals()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM recompute_accounts_deal_aggregates(NEW.accounts_id);
    ELSIF TG_OP = 'UPDATE' THEN
        PERFORM recompute_accounts_deal_aggregates(NEW.accounts_id);
        IF OLD.accounts_id IS DISTINCT FROM NEW.accounts_id THEN
            PERFORM recompute_accounts_deal_aggregates(OLD.accounts_id);
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM recompute_accounts_deal_aggregates(OLD.accounts_id);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_RECOMPUTE_FN)
    op.execute(_TRIGGER_FN)
    op.execute(
        """
        CREATE TRIGGER trg_salesorders_deal_aggregates
        AFTER INSERT OR UPDATE OR DELETE ON salesorders
        FOR EACH ROW
        EXECUTE FUNCTION trg_salesorders_recompute_account_deals();
        """
    )
    # Backfill: recompute aggregates for every account that currently
    # has at least one salesorder. Accounts with no orders keep their
    # current values (likely 0 — the schema default).
    op.execute(
        """
        SELECT recompute_accounts_deal_aggregates(accounts_id)
          FROM (SELECT DISTINCT accounts_id FROM salesorders
                 WHERE accounts_id IS NOT NULL) AS s;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_salesorders_deal_aggregates "
        "ON salesorders;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS trg_salesorders_recompute_account_deals();"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "recompute_accounts_deal_aggregates(BIGINT);"
    )
