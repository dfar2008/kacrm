"""Memory Substrate entity graph — charter 原语 ②.

Adds ``memory_entities`` + ``memory_edges`` on top of the existing
``memories`` table (created in 0002). Together the three form the
"时序 + 向量 + 图" triad the charter requires:

- ``memories``          — distilled facts/summaries with pgvector embedding
- ``memory_entities``   — extracted entities (people, orgs, products, …)
- ``memory_edges``      — typed relations between entities

Entities are cross-linked to their supporting memories via
``memory_entity_sources`` (M:N) so we can "time-travel": which piece of
distilled memory first told us that Alice works at ACME?

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-17
"""
from alembic import op

revision = "0015"
down_revision = "0014"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE memory_entities (
            id            BIGSERIAL PRIMARY KEY,
            entity_type   VARCHAR(32) NOT NULL,
              -- person | org | product | deal | lead | location | topic | misc
            canonical     VARCHAR(255) NOT NULL,
              -- canonical surface form; downstream dedup joins on (type, lower(canonical))
            aliases       JSONB NOT NULL DEFAULT '[]'::jsonb,
            attributes    JSONB NOT NULL DEFAULT '{}'::jsonb,
              -- free-form attrs: industry, title, mobile, …
            user_id       BIGINT REFERENCES users(id) ON DELETE CASCADE,
              -- scoping: NULL = org-shared, else per-user
            confidence    REAL NOT NULL DEFAULT 1.0,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX idx_memory_entities_canonical "
        "ON memory_entities (entity_type, lower(canonical), "
        "COALESCE(user_id, 0))"
    )
    op.execute(
        "CREATE INDEX idx_memory_entities_user "
        "ON memory_entities (user_id) WHERE user_id IS NOT NULL"
    )

    op.execute("""
        CREATE TABLE memory_edges (
            id            BIGSERIAL PRIMARY KEY,
            src_entity_id BIGINT NOT NULL REFERENCES memory_entities(id)
                                          ON DELETE CASCADE,
            dst_entity_id BIGINT NOT NULL REFERENCES memory_entities(id)
                                          ON DELETE CASCADE,
            relation      VARCHAR(64) NOT NULL,
              -- works_at | located_in | owns | reports_to | deal_for | …
            attributes    JSONB NOT NULL DEFAULT '{}'::jsonb,
            confidence    REAL NOT NULL DEFAULT 1.0,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (src_entity_id, dst_entity_id, relation)
        )
    """)
    op.execute(
        "CREATE INDEX idx_memory_edges_src "
        "ON memory_edges (src_entity_id, relation)"
    )
    op.execute(
        "CREATE INDEX idx_memory_edges_dst "
        "ON memory_edges (dst_entity_id, relation)"
    )

    # M:N link: which memory rows supply evidence for each entity
    op.execute("""
        CREATE TABLE memory_entity_sources (
            entity_id  BIGINT NOT NULL REFERENCES memory_entities(id)
                                        ON DELETE CASCADE,
            memory_id  BIGINT NOT NULL REFERENCES memories(id)
                                        ON DELETE CASCADE,
            role       VARCHAR(32),
              -- 'mention' | 'definition' | 'update' | 'correction'
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (entity_id, memory_id)
        )
    """)
    op.execute(
        "CREATE INDEX idx_memory_entity_sources_memory "
        "ON memory_entity_sources (memory_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_entity_sources")
    op.execute("DROP TABLE IF EXISTS memory_edges")
    op.execute("DROP TABLE IF EXISTS memory_entities")
