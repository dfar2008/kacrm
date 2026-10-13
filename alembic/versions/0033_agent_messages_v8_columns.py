"""agent_sessions/agent_messages: mirror sqlite SCHEMA_VERSION 6→8 columns

Hermes upstream v2026.4.23 bumped its sqlite ``hermes_state.SessionDB``
SCHEMA_VERSION from 6 → 8 with two new columns:

  - ``sessions.api_call_count INTEGER DEFAULT 0`` (v8) — tracks the
    actual provider API-call count per session (PR #13861, dashboard
    instrumentation).
  - ``messages.reasoning_content TEXT`` (v7) — preserves Kimi's
    provider-native reasoning_content separately from
    ``reasoning_details`` so multi-turn replay against Kimi keeps the
    chain-of-thought formatting upstream models expect.

PgSessionDB shadows the same API surface, so the PG tables need the
matching columns. Alembic 0031 captured the v6 layout; this migration
catches up to v8.

The new sqlite v7/v8 also adds a ``kv_store`` helper table used by the
auto-prune-at-startup feature (PR #14736 region). That table is
internal-only; SessionDB's public methods never expose it. No PG
mirror needed — if PgSessionDB ever needs prune semantics, ``alembic``
+ a celery beat is the natural fit, not a sqlite-side helper table.

Revision ID: 0033
Revises: 0032
"""

from alembic import op


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_sessions "
        "ADD COLUMN api_call_count BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE agent_messages "
        "ADD COLUMN reasoning_content TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE agent_messages DROP COLUMN IF EXISTS reasoning_content"
    )
    op.execute(
        "ALTER TABLE agent_sessions DROP COLUMN IF EXISTS api_call_count"
    )
