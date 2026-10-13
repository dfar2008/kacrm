"""specialist_reward_signals: per-specialist feedback ledger.

Charter §一原语 ⑥ Feedback Collector + ⑦ Training Loop. Captures
who-gets-credit-for-what when actions are approved/rejected:

  - "quality:approve" / "quality:reject" — the executing specialist's draft
  - "routing:bad" — edge-level signal (parent specialist routed wrong)
  - "objective:bad" — chain-root specialist took on a bad ask

See docs/2026-04-26-v23-specialist-runtime-design.md §2.5.D.

Schema reconciliation: the original 0035 declared
``gateway_actions.delegation_{parent,root}_draft_id`` and
``specialist_delegation_edges.{parent,child}_draft_id`` as BIGINT, but
``gateway_actions.id`` is a UUID. The runtime needs to use the action's
UUID as the draft identifier in the delegation chain, so we ALTER those
columns to TEXT here (forward-only; nullable; existing BIGINT values
cast cleanly to numeric strings).

Revision ID: 0037
Revises: 0036
"""
from alembic import op


revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reconcile draft-id types from BIGINT → TEXT so they can hold the
    # UUID strings used as gateway_actions.id.
    op.execute(
        "ALTER TABLE gateway_actions "
        "ALTER COLUMN delegation_parent_draft_id TYPE TEXT "
        "USING delegation_parent_draft_id::text"
    )
    op.execute(
        "ALTER TABLE gateway_actions "
        "ALTER COLUMN delegation_root_draft_id TYPE TEXT "
        "USING delegation_root_draft_id::text"
    )
    op.execute(
        "ALTER TABLE specialist_delegation_edges "
        "ALTER COLUMN parent_draft_id TYPE TEXT "
        "USING parent_draft_id::text"
    )
    op.execute(
        "ALTER TABLE specialist_delegation_edges "
        "ALTER COLUMN child_draft_id TYPE TEXT "
        "USING child_draft_id::text"
    )

    # Per-specialist reward-signal ledger.
    op.execute(
        "CREATE TABLE specialist_reward_signals ("
        "  id BIGSERIAL PRIMARY KEY, "
        "  draft_id TEXT NOT NULL, "
        "  specialist TEXT NOT NULL, "
        "  signal TEXT NOT NULL, "
        "  edge_id BIGINT, "
        "  created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX idx_reward_signals_draft "
        "ON specialist_reward_signals (draft_id)"
    )
    op.execute(
        "CREATE INDEX idx_reward_signals_specialist "
        "ON specialist_reward_signals (specialist)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_reward_signals_specialist")
    op.execute("DROP INDEX IF EXISTS idx_reward_signals_draft")
    op.execute("DROP TABLE IF EXISTS specialist_reward_signals")
    op.execute(
        "ALTER TABLE specialist_delegation_edges "
        "ALTER COLUMN child_draft_id TYPE BIGINT "
        "USING NULLIF(child_draft_id, '')::bigint"
    )
    op.execute(
        "ALTER TABLE specialist_delegation_edges "
        "ALTER COLUMN parent_draft_id TYPE BIGINT "
        "USING NULLIF(parent_draft_id, '')::bigint"
    )
    op.execute(
        "ALTER TABLE gateway_actions "
        "ALTER COLUMN delegation_root_draft_id TYPE BIGINT "
        "USING NULLIF(delegation_root_draft_id, '')::bigint"
    )
    op.execute(
        "ALTER TABLE gateway_actions "
        "ALTER COLUMN delegation_parent_draft_id TYPE BIGINT "
        "USING NULLIF(delegation_parent_draft_id, '')::bigint"
    )
