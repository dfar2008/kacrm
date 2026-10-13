"""Enable pgvector + wire embedding columns into the memory substrate.

Design notes:

``interaction_log`` is the raw append-only event stream (every turn, every
channel message). Embedding *every* raw row is wasteful — most turns are
short, noisy, and only matter as part of a session. We still add an
``embedding`` column there so short-horizon session retrieval works, but the
primary vector search surface is the new ``memories`` table: distilled facts,
summaries, and extracted entities that are the real substrate for RAG.

Dimension choice: 1536 matches OpenAI ``text-embedding-3-small`` and
``text-embedding-ada-002`` — the two most common defaults. Changing later
means a schema migration + re-embedding, so this is a committed choice.

HNSW params left at pgvector defaults (m=16, ef_construction=64). We pick
``vector_cosine_ops`` because every production embedding model we'll plug in
(OpenAI, BGE, Qwen3-embed, Voyage) normalizes for cosine similarity.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-15
"""
from __future__ import annotations

from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


EMBEDDING_DIM = 1536  # OpenAI text-embedding-3-small / ada-002


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enable the extension. Requires the DB role to be a superuser OR to have
    # the ``CREATE`` privilege on the database and the extension pre-authorized.
    # On a fresh local PG that's fine; in managed envs the ops team usually
    # pre-creates the extension out of band.
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ------------------------------------------------------------------
    # interaction_log: optional per-turn embedding for session-scoped recall.
    # We don't index it yet — at 1 row per turn, brute-force inside a session
    # window is cheaper than maintaining an HNSW index on a hot append table.
    # If/when we need cross-session retrieval on raw turns we'll add an HNSW
    # index in a later revision.
    # ------------------------------------------------------------------
    op.execute(
        f"ALTER TABLE interaction_log "
        f"ADD COLUMN embedding vector({EMBEDDING_DIM});"
    )

    # ------------------------------------------------------------------
    # memories: the distilled memory substrate.
    #
    # ``kind`` is free-form but we expect values like:
    #   'fact'        — atomic assertions extracted from conversations
    #   'summary'     — condensed session or thread summaries
    #   'preference'  — user/customer preferences
    #   'entity'      — extracted entity descriptions (Account, Contact, …)
    #   'skill'       — procedural / how-to memories for the agent
    #
    # ``scope`` narrows visibility:
    #   'user'  — shared across all of a user's agents
    #   'agent' — private to a single agent
    #   'org'   — shared across the whole org
    # ``scope='org'`` rows set ``user_id``/``agent_id`` to NULL.
    #
    # ``source_refs`` is a JSON array of source interaction_log ids /
    # record ids — provenance trail for every memory. This is what lets us
    # invalidate / re-derive memories when the underlying facts change.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE memories (
            id              BIGSERIAL PRIMARY KEY,
            user_id         BIGINT REFERENCES users(id) ON DELETE CASCADE,
            agent_id        BIGINT REFERENCES agents(id) ON DELETE CASCADE,
            scope           VARCHAR(16) NOT NULL DEFAULT 'user',
            kind            VARCHAR(32) NOT NULL,
            title           VARCHAR(255),
            content         TEXT NOT NULL,
            embedding       vector({EMBEDDING_DIM}),
            source_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,
            confidence      REAL NOT NULL DEFAULT 1.0,
            meta            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ,
            CONSTRAINT memories_scope_check
                CHECK (scope IN ('user', 'agent', 'org'))
        );
        """
    )

    # Conventional lookups.
    op.execute(
        "CREATE INDEX idx_memories_user_kind ON memories(user_id, kind) "
        "WHERE user_id IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX idx_memories_agent_kind ON memories(agent_id, kind) "
        "WHERE agent_id IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX idx_memories_scope_kind ON memories(scope, kind);"
    )
    op.execute(
        "CREATE INDEX idx_memories_expires ON memories(expires_at) "
        "WHERE expires_at IS NOT NULL;"
    )

    # HNSW on cosine distance — the main vector search index.
    # m=16, ef_construction=64 are pgvector defaults; tune later from logs.
    op.execute(
        "CREATE INDEX idx_memories_embedding_hnsw "
        "ON memories USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )

    # updated_at trigger
    op.execute(
        "CREATE TRIGGER trg_memories_updated_at BEFORE UPDATE ON memories "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memories;")
    op.execute("ALTER TABLE interaction_log DROP COLUMN IF EXISTS embedding;")
    # We deliberately do NOT drop the extension — other migrations may rely on
    # it and ``DROP EXTENSION`` is rarely what you want during a rollback.
