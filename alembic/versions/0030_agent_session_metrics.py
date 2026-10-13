"""agent_session_metrics: per-session aggregates projected from interactions

Charter §一原语 ③ — Projections are derived views over the Interaction
Log, not source-of-truth. Cloud's ``/api/analytics`` and the session list
need fast aggregates (token counts, costs, message counts, time bounds)
per agent session. Computing them at request time means a GROUP BY over
every interaction every page-load; storing them as a projection table
means the read is a B-tree lookup.

Source of truth stays in ``interactions``. This table is rebuildable from
scratch by ``TRUNCATE agent_session_metrics; INSERT ... FROM interactions
GROUP BY ...``. The trigger function below contains the rebuild rules
encoded as a per-row UPSERT, fired AFTER INSERT on ``interactions`` when
``kind = 'agent.message'``.

Why a trigger and not a celery cron:

  - Trigger fires synchronously in the same transaction, so reads after
    a successful emit() see the updated metrics — no eventual-consistency
    surprises in tests or in the dashboard right after an agent run.
  - Cost is one UPSERT per agent.message row; cheap.
  - If the trigger ever drifts from interactions truth, a single
    TRUNCATE + INSERT...SELECT rebuild restores it. The function is
    annotated below for that purpose.

Why no DELETE/UPDATE handling:

  - interactions is append-only by Charter (定律 1: events are first-class,
    state is derived). agent.message rows aren't supposed to be UPDATEd.
  - Manual cleanup that DELETEs rows is an out-of-band ops action; the
    projection is rebuildable, so a one-liner TRUNCATE + rebuild is the
    answer. Adding DELETE trigger logic would just be cycle-burning.

Revision ID: 0030
Revises: 0029
"""

from alembic import op


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Table: per (profile_id, session_id) aggregates.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE agent_session_metrics (
            id                  BIGSERIAL PRIMARY KEY,
            profile_id          TEXT NOT NULL,
            session_id          TEXT NOT NULL,
            agent_name          TEXT,
            user_id             BIGINT,
            model               TEXT,
            started_at          TIMESTAMPTZ NOT NULL,
            ended_at            TIMESTAMPTZ NOT NULL,
            message_count       INTEGER NOT NULL DEFAULT 0,
            input_tokens        BIGINT  NOT NULL DEFAULT 0,
            output_tokens       BIGINT  NOT NULL DEFAULT 0,
            cache_read_tokens   BIGINT  NOT NULL DEFAULT 0,
            cache_write_tokens  BIGINT  NOT NULL DEFAULT 0,
            reasoning_tokens    BIGINT  NOT NULL DEFAULT 0,
            estimated_cost_usd  NUMERIC(14, 6) NOT NULL DEFAULT 0,
            UNIQUE (profile_id, session_id)
        )
        """
    )
    # Hot read pattern: "list this user's recent sessions, newest first".
    op.execute(
        """
        CREATE INDEX idx_agent_session_metrics_user
            ON agent_session_metrics (user_id, started_at DESC)
        """
    )
    # Auxiliary: "what models has this profile used recently" for analytics
    # by_model breakdown (without it the by_model view would seq-scan).
    op.execute(
        """
        CREATE INDEX idx_agent_session_metrics_profile_model
            ON agent_session_metrics (profile_id, model)
        """
    )

    # ------------------------------------------------------------------
    # Trigger: AFTER INSERT on interactions, project agent.message rows
    # into agent_session_metrics.
    # ------------------------------------------------------------------
    # The function pulls fields out of NEW.actors / NEW.content using JSONB
    # operators; if the schema validator (emitter._validate_agent_message)
    # is doing its job these are guaranteed to be present.
    #
    # First-message-of-session UPSERT shape:
    #   - INSERT a fresh row with started_at = ended_at = NEW.occurred_at
    #     and message_count=1, tokens populated.
    # Subsequent message UPSERT shape:
    #   - Increment message_count + token counters
    #   - ended_at = greatest(existing, NEW.occurred_at) so out-of-order
    #     emits don't make ended_at go backwards.
    #   - started_at = least(existing, NEW.occurred_at) — same reason.
    #   - agent_name / model: keep existing (first writer wins) so an
    #     agent handoff inside a session doesn't overwrite the original.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_agent_session_metrics_upsert()
        RETURNS TRIGGER AS $$
        DECLARE
            v_profile_id  TEXT;
            v_session_id  TEXT;
            v_agent_name  TEXT;
            v_user_id     BIGINT;
            v_model       TEXT;
            v_input       BIGINT;
            v_output      BIGINT;
            v_cache_r     BIGINT;
            v_cache_w     BIGINT;
            v_reasoning   BIGINT;
            v_cost        NUMERIC(14, 6);
        BEGIN
            -- Bail fast for unrelated kinds.
            IF NEW.kind <> 'agent.message' THEN
                RETURN NEW;
            END IF;

            -- Pull projection keys. ``actors`` is a JSONB array; use a
            -- subquery + jsonb_array_elements rather than ``->`` indexing
            -- so a missing entry just yields NULL instead of an error.
            SELECT a->>'profile_id', a->>'name'
              INTO v_profile_id, v_agent_name
              FROM jsonb_array_elements(NEW.actors) AS a
             WHERE a->>'type' = 'agent'
             LIMIT 1;

            SELECT (a->>'id')::BIGINT
              INTO v_user_id
              FROM jsonb_array_elements(NEW.actors) AS a
             WHERE a->>'type' = 'user'
             LIMIT 1;

            v_session_id := NEW.content->>'session_id';

            -- Required keys absent → trigger silently skips. The validator
            -- already rejected such rows at emit() time; this is just a
            -- defensive guard if something bypassed the validator (raw
            -- INSERT in a migration / repair script).
            IF v_session_id IS NULL OR v_profile_id IS NULL THEN
                RETURN NEW;
            END IF;

            v_model := NEW.content->>'model';
            v_input     := COALESCE((NEW.content->'tokens'->>'input')::BIGINT, 0);
            v_output    := COALESCE((NEW.content->'tokens'->>'output')::BIGINT, 0);
            v_cache_r   := COALESCE((NEW.content->'tokens'->>'cache_read')::BIGINT, 0);
            v_cache_w   := COALESCE((NEW.content->'tokens'->>'cache_write')::BIGINT, 0);
            v_reasoning := COALESCE((NEW.content->'tokens'->>'reasoning')::BIGINT, 0);
            v_cost      := COALESCE((NEW.content->>'estimated_cost_usd')::NUMERIC, 0);

            INSERT INTO agent_session_metrics (
                profile_id, session_id, agent_name, user_id, model,
                started_at, ended_at, message_count,
                input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens,
                reasoning_tokens, estimated_cost_usd
            ) VALUES (
                v_profile_id, v_session_id, v_agent_name, v_user_id, v_model,
                NEW.occurred_at, NEW.occurred_at, 1,
                v_input, v_output,
                v_cache_r, v_cache_w,
                v_reasoning, v_cost
            )
            ON CONFLICT (profile_id, session_id) DO UPDATE SET
                started_at         = LEAST(agent_session_metrics.started_at, EXCLUDED.started_at),
                ended_at           = GREATEST(agent_session_metrics.ended_at, EXCLUDED.ended_at),
                message_count      = agent_session_metrics.message_count + 1,
                input_tokens       = agent_session_metrics.input_tokens + EXCLUDED.input_tokens,
                output_tokens      = agent_session_metrics.output_tokens + EXCLUDED.output_tokens,
                cache_read_tokens  = agent_session_metrics.cache_read_tokens + EXCLUDED.cache_read_tokens,
                cache_write_tokens = agent_session_metrics.cache_write_tokens + EXCLUDED.cache_write_tokens,
                reasoning_tokens   = agent_session_metrics.reasoning_tokens + EXCLUDED.reasoning_tokens,
                estimated_cost_usd = agent_session_metrics.estimated_cost_usd + EXCLUDED.estimated_cost_usd,
                -- agent_name / user_id / model: keep existing (first writer wins).
                agent_name         = COALESCE(agent_session_metrics.agent_name, EXCLUDED.agent_name),
                user_id            = COALESCE(agent_session_metrics.user_id, EXCLUDED.user_id),
                model              = COALESCE(agent_session_metrics.model, EXCLUDED.model);

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_session_metrics_upsert
            AFTER INSERT ON interactions
            FOR EACH ROW
            EXECUTE FUNCTION fn_agent_session_metrics_upsert()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_agent_session_metrics_upsert ON interactions")
    op.execute("DROP FUNCTION IF EXISTS fn_agent_session_metrics_upsert()")
    op.execute("DROP INDEX IF EXISTS idx_agent_session_metrics_profile_model")
    op.execute("DROP INDEX IF EXISTS idx_agent_session_metrics_user")
    op.execute("DROP TABLE IF EXISTS agent_session_metrics")
