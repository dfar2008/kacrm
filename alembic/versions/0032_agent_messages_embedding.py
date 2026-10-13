"""agent_messages.embedding: pgvector column for semantic search

Charter §一原语 ② — Memory Substrate is a unified semantic layer. The
agent's session search tool (``hermes/tools/session_search_tool.py``)
historically relied on sqlite FTS5 lexical match, then asked Gemini
Flash to summarize the top hits — i.e. it was already trying to bolt
semantic-ish behaviour onto a lexical primitive.

This migration adds an ``embedding vector(1536)`` column to
``agent_messages`` so PgSessionDB.search_messages can do real semantic
search via pgvector cosine similarity instead. Embedding dimension is
locked at 1536 to match ``EMBEDDING_DIM`` in
:mod:`hermes_cloud.app.memory.embedder` and the ``memories.embedding``
column from migration 0002.

Population strategy:
  Embeddings are filled asynchronously by ``memory.ingest.ingest_batch``
  (the existing 5-min beat task). agent_messages rows go in with
  embedding=NULL; the next beat scans, embeds, fills. 5-min lag is
  acceptable because the search use case is "find a past session" —
  recency-of-last-second isn't required.

  Embedding-on-write was considered and rejected: would add 50-200ms
  to every agent.append_message call, bottlenecking the agent loop.

Index:
  HNSW with ``vector_cosine_ops`` — same operator class as the
  ``memories.embedding`` index from 0002. HNSW (not IVFFlat) because
  we expect agent_messages to grow continuously without a clean
  retraining boundary.

Revision ID: 0032
Revises: 0031
"""

from alembic import op


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_messages "
        "ADD COLUMN embedding vector(1536)"
    )
    # HNSW for cosine similarity. Built lazily — empty index is fast.
    op.execute(
        """
        CREATE INDEX idx_agent_messages_embedding
            ON agent_messages
         USING hnsw (embedding vector_cosine_ops)
         WHERE embedding IS NOT NULL
        """
    )
    # Helper view: messages still needing embedding. ingest_batch reads
    # this to find the work queue, so the SQL stays in one place and
    # the beat task itself is one-liner.
    op.execute(
        """
        CREATE OR REPLACE VIEW agent_messages_pending_embedding AS
        SELECT id, session_id, content
          FROM agent_messages
         WHERE embedding IS NULL
           AND content IS NOT NULL
           AND length(btrim(content)) > 0
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS agent_messages_pending_embedding")
    op.execute("DROP INDEX IF EXISTS idx_agent_messages_embedding")
    op.execute(
        "ALTER TABLE agent_messages DROP COLUMN IF EXISTS embedding"
    )
