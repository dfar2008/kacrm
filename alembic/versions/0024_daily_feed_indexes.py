"""Performance indexes for ``/api/insights/daily_feed``.

The daily-feed endpoint fans out three time-windowed ``ORDER BY ... DESC
LIMIT N`` queries per request. When the tables grow past a few million
rows, the planner falls back to full scans because the existing indexes
don't cover the (scope, time-desc) access pattern this endpoint uses.

Three targeted adds:

1. **``rl_agent_drafts(profile_id, created_at DESC)``** — daily_feed
   filters ``WHERE p.user_id = %s AND d.created_at > %s`` then sorts by
   ``created_at DESC LIMIT``.  The existing ``(profile_id, agent_name)``
   and ``(created_at DESC)`` indexes each cover half; a composite makes
   the whole thing a single index scan. Also used by Pool specialist
   detail + Pool rollup queries.
2. **``coach_hints(user_id, created_at DESC)``** — the existing
   ``ix_coach_hints_user_unread`` is a **partial** index
   (``WHERE read_at IS NULL AND dismissed_at IS NULL``), so it can't
   serve daily_feed (which returns read / dismissed hints too, tagged
   via ``is_read`` / ``is_dismissed`` on the card). Add the non-partial
   variant for the feed path.
3. **GIN on ``interactions(actors)``** — daily_feed scopes by
   ``actors @> '[{"type":"user","id":N}]'::jsonb``. ``subjects`` already
   has a GIN index; ``actors`` is queried the same way and was missed.

All guarded with ``IF NOT EXISTS`` so partial rollouts and re-runs are
safe. Non-concurrent to keep the migration transactional — if someone
runs this against a huge prod table later, switch to
``CREATE INDEX CONCURRENTLY`` outside the alembic transaction.

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-19
"""
from __future__ import annotations

from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. rl_agent_drafts: (profile_id, created_at DESC)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rl_drafts_profile_created
            ON rl_agent_drafts (profile_id, created_at DESC)
        """
    )
    # 2. coach_hints: non-partial (user_id, created_at DESC) for feed path
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_coach_hints_user_created
            ON coach_hints (user_id, created_at DESC)
        """
    )
    # 3. interactions: GIN on actors (mirrors existing GIN on subjects)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_interactions_actors
            ON interactions USING gin (actors)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interactions_actors")
    op.execute("DROP INDEX IF EXISTS idx_coach_hints_user_created")
    op.execute("DROP INDEX IF EXISTS idx_rl_drafts_profile_created")
