"""Attachment persistence + polymorphic record association.

attachments        — one row per uploaded file (identity + local storage path),
                     created unconditionally at upload time (audit trail).
attachment_links   — polymorphic many-to-many: an attachment <-> a CRM record,
                     written at emit_business_write when the agent creates a
                     record in the same conversation.

See docs/superpowers/specs/2026-06-17-attachment-record-association-design.md

Revision ID: 0102
Revises: 0101
Create Date: 2026-06-17
"""
from __future__ import annotations

from alembic import op


revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id              BIGSERIAL PRIMARY KEY,
            owner_user_id   BIGINT NOT NULL,
            profile_id      VARCHAR(64),
            conversation_id TEXT,
            filename        TEXT NOT NULL,
            mime_type       TEXT,
            kind            TEXT,
            sha256          TEXT NOT NULL,
            size_bytes      BIGINT NOT NULL,
            stored_path     TEXT NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT now(),
            deleted         BOOLEAN DEFAULT FALSE
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_attachments_sha_owner
            ON attachments(owner_user_id, sha256) WHERE deleted = false
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_attachments_conversation
            ON attachments(conversation_id) WHERE conversation_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS attachment_links (
            id               BIGSERIAL PRIMARY KEY,
            attachment_id    BIGINT NOT NULL REFERENCES attachments(id),
            target_module    TEXT NOT NULL,
            target_record_id BIGINT NOT NULL,
            interaction_id   UUID,
            created_at       TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT ux_attlink UNIQUE (attachment_id, target_module, target_record_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_attlinks_target "
        "ON attachment_links(target_module, target_record_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_attlinks_attachment "
        "ON attachment_links(attachment_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS attachment_links")
    op.execute("DROP TABLE IF EXISTS attachments")
