"""interactions: B-tree functional index for agent.message session lookup

Charter §一定律 1 + 原语 ① — agent conversation messages are first-class
events landing in ``interactions`` with kind='agent.message'. The hot read
pattern from cloud's ``/api/sessions/{id}/messages`` endpoint is::

    SELECT * FROM interactions
     WHERE kind = 'agent.message'
       AND content->>'session_id' = $1
     ORDER BY occurred_at;

Without an index this scans the whole table. Existing GIN indexes don't
help here:

  - ``idx_interactions_subjects`` (0007): GIN on ``subjects`` — wrong column.
  - ``idx_interactions_actors`` (0024): GIN on ``actors`` — wrong column.
  - ``idx_interactions_search_tsv`` (0027): GIN tsvector — for full-text,
    not exact-match.
  - JSONB ``jsonb_path_ops`` indexes accelerate ``@>`` containment, not the
    ``->>'session_id' = ?`` scalar equality predicate the read pattern uses.

A B-tree functional index on ``(kind, (content->>'session_id'), occurred_at)``
gives the planner a covering ordered scan: filter by kind + session_id,
return rows already sorted. The leading ``kind`` lets other future kinds
(``email`` / ``call`` / ``meeting``) reuse the same index when they need
session_id lookups too — no per-kind index proliferation.

Revision ID: 0029
Revises: 0028
"""

from alembic import op


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_interactions_kind_session
            ON interactions
                (kind, (content->>'session_id'), occurred_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interactions_kind_session")
