"""v0.21.6 durable transcript identity and live model-route history."""
from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


def upgrade():
    # Session listing/writes acquire sessions before messages. Acquire both
    # up front in that order, before ALTER/backfill can take the reverse path.
    op.execute("SET LOCAL lock_timeout = '30s'")
    op.execute("LOCK TABLE agent_sessions, agent_messages IN ACCESS EXCLUSIVE MODE")
    for column, kind in (("message_uid", "TEXT"), ("absorbed_message_uids", "JSONB"),
                         ("tool_call_uids", "JSONB"), ("tool_call_uid", "TEXT"),
                         ("effect_disposition", "TEXT")):
        op.execute(f"ALTER TABLE agent_messages ADD COLUMN {column} {kind}")
    # Existing rows need an id stable across reloads too; no uuid extension
    # dependency. These ids are opaque, just like upstream mint_uid().
    op.execute("UPDATE agent_messages SET message_uid = md5(random()::text || id::text || clock_timestamp()::text) WHERE message_uid IS NULL")
    for column, kind in (("cwd", "TEXT"), ("git_branch", "TEXT"), ("git_repo_root", "TEXT"),
                         ("git_metadata_generation", "BIGINT NOT NULL DEFAULT 0")):
        op.execute(f"ALTER TABLE agent_sessions ADD COLUMN {column} {kind}")
    op.execute("""CREATE TABLE agent_session_model_usage (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
        model TEXT NOT NULL, billing_provider TEXT NOT NULL,
        billing_base_url TEXT NOT NULL, billing_mode TEXT NOT NULL,
        task TEXT NOT NULL DEFAULT '', api_call_count BIGINT NOT NULL DEFAULT 0,
        last_seen DOUBLE PRECISION NOT NULL)""")
    op.execute("CREATE INDEX idx_agent_session_model_usage_recent ON agent_session_model_usage(session_id, last_seen DESC, id DESC) WHERE task = ''")


def downgrade():
    op.execute("SET LOCAL lock_timeout = '30s'")
    op.execute("LOCK TABLE agent_sessions, agent_messages IN ACCESS EXCLUSIVE MODE")
    op.execute("DROP TABLE agent_session_model_usage")
    for column in ("git_metadata_generation", "git_repo_root", "git_branch", "cwd"):
        op.execute(f"ALTER TABLE agent_sessions DROP COLUMN {column}")
    for column in ("effect_disposition", "tool_call_uid", "tool_call_uids", "absorbed_message_uids", "message_uid"):
        op.execute(f"ALTER TABLE agent_messages DROP COLUMN {column}")
