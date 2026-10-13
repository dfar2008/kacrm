"""agent_sessions + agent_messages: PG-backed SessionDB tables (Charter §九)

Spike for migrating hermes' SessionDB from per-machine sqlite to the
cloud's shared PostgreSQL. Mirrors the sqlite schema in
``hermes/hermes_state.py`` (SCHEMA_VERSION=6) so PgSessionDB can be a
drop-in replacement.

Why two new tables (vs reusing ``interactions``)?

The ``interactions`` table already gets agent.message rows via the
hermes-patches/09 tap, but it's a kind-agnostic Interaction Log:
schema is (kind, actors, subjects, content, source, ...). Hermes'
SessionDB API expects a typed schema with explicit columns for
session metadata (model / billing / token counters / title / etc.) and
per-message fields (role / tool_call_id / reasoning_details / etc.).
Squeezing those into ``content`` JSONB and pulling them back out on
every read would force expensive jsonb extracts and lose the type
safety the upstream test suite expects.

So: ``agent_sessions`` + ``agent_messages`` are the canonical PG
backing for SessionDB. The existing ``interactions`` agent.message
events (alembic 0029/0030 + patch 09) become the **Interaction Log
projection** of these — a separate concern. Charter compliance:

- Single DB ✅ (both tables in cloud's PG, no per-machine sqlite)
- Single source of truth for conversations ✅ (agent_messages is the
  source; interactions agent.message rows are derivable from it via
  cron projection — to be wired in a follow-up commit)

Revision ID: 0031
Revises: 0030
"""

from alembic import op


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_sessions (
            id                  TEXT PRIMARY KEY,
            source              TEXT NOT NULL,
            user_id             TEXT,
            model               TEXT,
            model_config        JSONB,
            system_prompt       TEXT,
            parent_session_id   TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
            started_at          DOUBLE PRECISION NOT NULL,
            ended_at            DOUBLE PRECISION,
            end_reason          TEXT,
            message_count       INTEGER NOT NULL DEFAULT 0,
            tool_call_count     INTEGER NOT NULL DEFAULT 0,
            input_tokens        BIGINT  NOT NULL DEFAULT 0,
            output_tokens       BIGINT  NOT NULL DEFAULT 0,
            cache_read_tokens   BIGINT  NOT NULL DEFAULT 0,
            cache_write_tokens  BIGINT  NOT NULL DEFAULT 0,
            reasoning_tokens    BIGINT  NOT NULL DEFAULT 0,
            billing_provider    TEXT,
            billing_base_url    TEXT,
            billing_mode        TEXT,
            estimated_cost_usd  DOUBLE PRECISION,
            actual_cost_usd     DOUBLE PRECISION,
            cost_status         TEXT,
            cost_source         TEXT,
            pricing_version     TEXT,
            title               TEXT
        )
        """
    )
    # Indexes mirror the sqlite ones (hermes_state.py lines 87-90).
    op.execute(
        "CREATE INDEX idx_agent_sessions_source ON agent_sessions(source)"
    )
    op.execute(
        "CREATE INDEX idx_agent_sessions_parent "
        "ON agent_sessions(parent_session_id)"
    )
    op.execute(
        "CREATE INDEX idx_agent_sessions_started "
        "ON agent_sessions(started_at DESC)"
    )
    # Title uniqueness — partial unique index (NULL titles allowed multiple).
    op.execute(
        "CREATE UNIQUE INDEX idx_agent_sessions_title_unique "
        "ON agent_sessions(title) WHERE title IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE agent_messages (
            id                       BIGSERIAL PRIMARY KEY,
            session_id               TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            role                     TEXT NOT NULL,
            content                  TEXT,
            tool_call_id             TEXT,
            tool_calls               JSONB,
            tool_name                TEXT,
            timestamp                DOUBLE PRECISION NOT NULL,
            token_count              INTEGER,
            finish_reason            TEXT,
            reasoning                TEXT,
            reasoning_details        JSONB,
            codex_reasoning_items    JSONB
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_messages_session "
        "ON agent_messages(session_id, timestamp, id)"
    )
    # Full-text search via tsvector — replaces sqlite's FTS5 virtual table.
    # ``simple`` config to match the rest of the system (interactions also
    # uses simple in alembic 0027).
    op.execute(
        """
        CREATE INDEX idx_agent_messages_fts
            ON agent_messages
         USING gin (to_tsvector('simple', coalesce(content, '')))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_messages_fts")
    op.execute("DROP INDEX IF EXISTS idx_agent_messages_session")
    op.execute("DROP TABLE IF EXISTS agent_messages")
    op.execute("DROP INDEX IF EXISTS idx_agent_sessions_title_unique")
    op.execute("DROP INDEX IF EXISTS idx_agent_sessions_started")
    op.execute("DROP INDEX IF EXISTS idx_agent_sessions_parent")
    op.execute("DROP INDEX IF EXISTS idx_agent_sessions_source")
    op.execute("DROP TABLE IF EXISTS agent_sessions")
