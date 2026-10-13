"""Cache LLM-generated suggested questions per record.

Charter §定律 5: AI drives even the *question authorship*. Phase A of
this work (commit 0cd2862 + 242e314) made suggested_questions
signal-driven via fixed templates — better than the old hard-coded
strings, but still templates. Phase C lets a small LLM call generate
3 fully-contextual questions for each record (using its summary +
hints + recent interactions), and we cache the result so subsequent
opens are instant + free.

Cache key: ``(module, record_id)``.

``signature`` is a content hash over the inputs that should
invalidate the cache:
  - signals on the active hints (sorted, dedup)
  - id of the most recent interaction touching this record
  - record_name (renamed records get fresh phrasing)

When the signature differs from what's cached, the route regenerates.
We also TTL on ``generated_at`` so a record that's been silent for a
day still gets re-asked occasionally (LLM gets smarter, prompts evolve).

Revision ID: 0046
Revises: 0045
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op


revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS record_question_cache (
            module TEXT NOT NULL,
            record_id BIGINT NOT NULL,
            signature TEXT NOT NULL,
            questions JSONB NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (module, record_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_record_question_cache_generated_at
            ON record_question_cache(generated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS record_question_cache")
