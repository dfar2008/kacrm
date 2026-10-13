"""record_writes: cross-agent CRM record write tracking.

Charter §一原语 ⑤ Action Gateway extension. Tracks which agent /
profile is currently writing which CRM record (module + record_id),
so concurrent specialists can detect and avoid overwrite collisions.

Two-tier coordination:
  (1) Hard lock: ``begin_write`` raises if another agent already has an
      open (un-committed, un-rolled-back) write intent on the same
      ``(module, record_id)``.
  (2) Soft warning: ``writes_since`` reports committed writes by other
      agents that landed after this token's begin — caller (crm_write)
      can warn the agent and let it decide abort vs. force.

Adds ``gateway_actions.record_write_token BIGINT`` so each pending
action carries a pointer back to its open intent — the dispatcher uses
it to commit/rollback when the action transitions to executed/rejected.

See docs/2026-04-25-deferred-upstream-features.md §A2.

Revision ID: 0038
Revises: 0037
"""
from alembic import op


revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE record_writes ("
        "  id BIGSERIAL PRIMARY KEY, "
        "  module TEXT NOT NULL, "
        "  record_id BIGINT NOT NULL, "
        "  agent_name TEXT NOT NULL, "
        "  profile_id TEXT NOT NULL, "
        "  began_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "  committed_at TIMESTAMPTZ, "
        "  rolled_back_at TIMESTAMPTZ, "
        "  fields JSONB"
        ")"
    )
    op.execute(
        "CREATE INDEX idx_record_writes_lookup "
        "ON record_writes (module, record_id) "
        "WHERE committed_at IS NULL AND rolled_back_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_record_writes_agent "
        "ON record_writes (agent_name, began_at DESC)"
    )
    op.execute(
        "ALTER TABLE gateway_actions "
        "ADD COLUMN record_write_token BIGINT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gateway_actions DROP COLUMN IF EXISTS record_write_token"
    )
    op.execute("DROP INDEX IF EXISTS idx_record_writes_agent")
    op.execute("DROP INDEX IF EXISTS idx_record_writes_lookup")
    op.execute("DROP TABLE IF EXISTS record_writes")
