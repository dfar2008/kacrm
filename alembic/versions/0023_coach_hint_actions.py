"""Audit + idempotency ledger for coach-hint card actions.

When a user taps ``[我已处理]`` / ``[稍后提醒]`` on a Feishu card the
dispatcher writes a row here before mutating ``coach_hints``. Two
reasons:

1. **Idempotency.** Feishu can re-deliver the same action (network
   retry, user double-tap) — ``(hint_id, action)`` is a unique pair
   per logical click. A second insert for the same pair is silently
   swallowed.
2. **Audit.** We want to know WHO closed a hint, WHEN, and THROUGH
   WHICH CHANNEL when analyzing coach-hint effectiveness later.

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-18
"""
from __future__ import annotations

from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS coach_hint_actions (
            id              BIGSERIAL   PRIMARY KEY,
            hint_id         BIGINT      NOT NULL,
            user_id         BIGINT      NOT NULL,
            action          TEXT        NOT NULL,
            actor_channel   TEXT        NULL,
            actor_open_id   TEXT        NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS coach_hint_actions_hint_action_uq
            ON coach_hint_actions (hint_id, action)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS coach_hint_actions_user_created_idx
            ON coach_hint_actions (user_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS coach_hint_actions")
