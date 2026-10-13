"""Persist Coach hints — turn the live /insights/weekly scan into a
real push-style notification with read / dismiss state.

Before this migration the CoachAgent heuristics were recomputed on
every request to ``/insights/weekly``. That meant:

* no red-dot on pages the user hadn't opened
* no history of what was surfaced or when
* re-reading the same advice every time

Charter §168 demands the product **tells** the user what to pay
attention to. This table lets a Celery job run the heuristics on a
schedule, upsert what it finds here, and let the UI render unread
counts + dismissal state like a proper inbox.

Column shape
------------
``(user_id, module, record_id, signal)`` is unique — same signal on
the same record is the *same* hint, re-surfacing doesn't create a
duplicate row. Payload carries the volatile bits (record_name, days
since contact, suggested next step) so the API doesn't need to
re-resolve module metadata at read time.

``read_at`` NULL → unread (badge counts it)
``dismissed_at`` NULL → still in the inbox

Partial index on unread rows keeps the red-dot query cheap even once
the table grows.

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-18
"""
from __future__ import annotations

from alembic import op


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS coach_hints (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            module TEXT NOT NULL,
            record_id BIGINT NOT NULL,
            signal TEXT NOT NULL,
            severity TEXT NOT NULL,
            hint TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            read_at TIMESTAMPTZ NULL,
            dismissed_at TIMESTAMPTZ NULL,
            CONSTRAINT coach_hints_uniq_signal
                UNIQUE (user_id, module, record_id, signal)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_coach_hints_user_unread
            ON coach_hints(user_id, created_at DESC)
            WHERE read_at IS NULL AND dismissed_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_coach_hints_record
            ON coach_hints(module, record_id)
            WHERE dismissed_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_coach_hints_record")
    op.execute("DROP INDEX IF EXISTS ix_coach_hints_user_unread")
    op.execute("DROP TABLE IF EXISTS coach_hints")
