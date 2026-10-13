"""Cache LLM-generated daily brief per (user_id, module).

Companion to ``record_question_cache`` (0046). Where that one caches
contextual questions for ONE record, this caches a higher-level
"AI's read on your day" brief used by the Copilot panel's empty
state on the event-stream and per-module daily dashboards.

Charter §定律 5: list-mode Copilot must be as proactive as the
record-mode one. Without this, opening /crm or /crm/accounts puts
the user in front of a silent chat box. With this, they see a
one-line summary + cross-record AI insights + 3 verb-led prompts —
identical pattern to RecordInsightCard, just at the day/module
granularity.

Cache key:
  - user_id  (each user gets their own — list-mode chats are
              user-scoped, unlike record-mode which is record-scoped)
  - module   (TEXT or empty string for the cross-module global view
              at /crm — empty string instead of NULL keeps the PK
              uniqueness check simple)

Signature inputs (hash invalidation):
  - date (so a new day always regenerates)
  - aggregate counts of today's events (interactions / outcomes /
    coach_hints): when the user actually does things, the summary
    should re-roll
  - module
  - user_id

TTL: 24h (same as record_question_cache); within a day the cache
hit path is essentially free. Beyond a day the date in the
signature naturally invalidates.

Revision ID: 0047
Revises: 0046
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_brief_cache (
            user_id INTEGER NOT NULL,
            module TEXT NOT NULL DEFAULT '',
            signature TEXT NOT NULL,
            brief JSONB NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, module)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_daily_brief_cache_generated_at
            ON daily_brief_cache(generated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_brief_cache")
