"""specialist_prompt_audit table — v3.3.2b.

Records the SHA-256 of every specialist system prompt that is actually
sent to the LLM, plus where the call originated (root invocation or
child spawn) and the optional draft id it produced.

Schema introduced in v3.3.2b — *table + helper only*; the actual
``record_specialist_prompt_audit(...)`` call sites land in v3.3.2d
(root invocation builder) and v3.3.2h (child spawn path).

Revision ID: 0045
Revises: 0042
"""
from __future__ import annotations

from alembic import op


revision = "0045"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS specialist_prompt_audit (
            id              BIGSERIAL PRIMARY KEY,
            profile_id      TEXT      NOT NULL,
            specialist_name TEXT      NOT NULL,
            prompt_hash     TEXT      NOT NULL,
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            called_from     TEXT      NOT NULL,
            draft_id        BIGINT,
            metadata        JSONB     NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT specialist_prompt_audit_called_from_chk
                CHECK (called_from IN ('root_invocation', 'child_spawn'))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            uq_specialist_prompt_audit_dedup
            ON specialist_prompt_audit
            (profile_id, specialist_name, prompt_hash, recorded_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_specialist_prompt_audit_lookup
            ON specialist_prompt_audit
            (profile_id, specialist_name, recorded_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_specialist_prompt_audit_hash
            ON specialist_prompt_audit (prompt_hash)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_specialist_prompt_audit_hash")
    op.execute("DROP INDEX IF EXISTS idx_specialist_prompt_audit_lookup")
    op.execute("DROP INDEX IF EXISTS uq_specialist_prompt_audit_dedup")
    op.execute("DROP TABLE IF EXISTS specialist_prompt_audit")
