"""Idempotency ledger for the weekly digest push.

The Celery beat ``weekly_digest_push`` task fires Monday morning and
iterates every user that has a home-channel binding. We don't want
one retry / worker crash / beat misfire to dispatch the same week's
digest twice — the user would see duplicate messages.

This table is the idempotency key: ``(user_id, week_start)`` is a
primary key, so inserting a row gates the send. The task inserts
first (on conflict do nothing), and only sends when the row count
reports a fresh insert.

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-18
"""
from __future__ import annotations

from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_digest_runs (
            user_id        TEXT        NOT NULL,
            week_start     DATE        NOT NULL,
            dispatched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            platform       TEXT        NULL,
            channel_id     TEXT        NULL,
            message_id     TEXT        NULL,
            ok             BOOLEAN     NOT NULL DEFAULT FALSE,
            error          TEXT        NULL,
            PRIMARY KEY (user_id, week_start)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS weekly_digest_runs")
