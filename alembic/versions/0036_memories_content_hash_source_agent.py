"""v2.3 specialist runtime — memory dedupe + per-specialist attribution.

Adds two columns to ``memories``:

* ``content_hash TEXT`` — sha256 of the *normalized* content (see
  ``hermes_cloud.app.memory.normalize.content_hash``). The upsert path
  uses this to short-circuit duplicate writes from the same specialist
  (Charter §2.5.E dedupe).
* ``source_agent TEXT`` — specialist *name* string ("research", "intake",
  …). Distinct from the existing ``agent_id`` FK which references the
  tracked ``agents`` row. Specialists are scoped at the registry level,
  not as agent rows, so we attribute by name.

A partial index on ``(scope, source_agent, content_hash)`` keeps the
dedupe lookup cheap while leaving rows that never compute a hash (e.g.
legacy ingest paths) out of the index.

The schema is forward-only safe: both columns are nullable, the index is
partial on ``content_hash IS NOT NULL`` so existing rows are unaffected.

Revision ID: 0036
Revises: 0035
"""

from alembic import op


revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memories "
        "ADD COLUMN content_hash TEXT, "
        "ADD COLUMN source_agent TEXT"
    )
    op.execute(
        "CREATE INDEX idx_memories_dedupe "
        "ON memories (scope, source_agent, content_hash) "
        "WHERE content_hash IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memories_dedupe")
    op.execute(
        "ALTER TABLE memories "
        "DROP COLUMN IF EXISTS source_agent, "
        "DROP COLUMN IF EXISTS content_hash"
    )
