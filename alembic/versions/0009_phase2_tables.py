"""Phase 2 tables — Action Gateway, Trust Engine, RL training data.

Plan §4.4–4.6 + §8–9. These tables support:

* **gateway_actions** — unified ledger for every Agent-produced side-effect.
  Every external action (CRM write, email send, workflow submit) gets a
  row here *before* execution, so the Trust Engine can gate it and the
  revert API can undo it.

* **trust_levels** — per (profile × agent × action_kind) trust score.
  Starts at L1 (require_approval) for all; Phase 4 auto-adjusts.

* **trust_decisions** — audit trail for every Trust Engine verdict on a
  gateway_action.

* **rl_agent_drafts** — the primary training-data table. Every Agent
  draft (field extraction, email, next-action) lands here with its
  input context, model output, and eventual human disposition
  (adopted / edited / rejected). Phase 3's sample_builder reads this.

* **rl_outcome_events** — delayed-reward signals (deal won/lost, stage
  advanced, email replied, customer churned). Phase 3's reward function
  joins these back to the agent actions that preceded them.
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- RL agent drafts (plan §4.4) ------------------------------------
    op.execute(
        """
        CREATE TABLE rl_agent_drafts (
            id              BIGSERIAL PRIMARY KEY,
            profile_id      VARCHAR(64) REFERENCES profiles(id) ON DELETE SET NULL,
            agent_name      TEXT NOT NULL,
            draft_type      TEXT,
            input_context   JSONB,
            draft_content   JSONB,
            human_edit      JSONB,
            adopted         BOOLEAN,
            edit_distance   DOUBLE PRECISION,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            adopted_at      TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_rl_drafts_profile_agent "
        "ON rl_agent_drafts (profile_id, agent_name);"
    )
    op.execute(
        "CREATE INDEX idx_rl_drafts_created "
        "ON rl_agent_drafts (created_at DESC);"
    )

    # -- RL outcome events (plan §4.4) ----------------------------------
    op.execute(
        """
        CREATE TABLE rl_outcome_events (
            id                BIGSERIAL PRIMARY KEY,
            event_type        TEXT NOT NULL,
            ref_module        TEXT,
            ref_id            BIGINT,
            agent_attribution JSONB,
            value             NUMERIC(14, 2),
            occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_rl_outcomes_ref "
        "ON rl_outcome_events (ref_module, ref_id);"
    )

    # -- Gateway actions (plan §4.5) ------------------------------------
    op.execute(
        """
        CREATE TABLE gateway_actions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            profile_id      VARCHAR(64) REFERENCES profiles(id) ON DELETE SET NULL,
            agent_name      TEXT,
            kind            TEXT NOT NULL,
            target          JSONB,
            params          JSONB,
            trust_level     INT,
            state           TEXT NOT NULL DEFAULT 'pending_approval',
            approver        BIGINT REFERENCES users(id) ON DELETE SET NULL,
            approved_at     TIMESTAMPTZ,
            executed_at     TIMESTAMPTZ,
            revert_token    JSONB,
            reverted_at     TIMESTAMPTZ,
            draft_id        BIGINT REFERENCES rl_agent_drafts(id) ON DELETE SET NULL,
            interaction_id  UUID REFERENCES interactions(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_gateway_state "
        "ON gateway_actions (state) WHERE state != 'executed';"
    )
    op.execute(
        "CREATE INDEX idx_gateway_profile "
        "ON gateway_actions (profile_id, created_at DESC);"
    )

    # -- Trust levels (plan §4.6) ---------------------------------------
    op.execute(
        """
        CREATE TABLE trust_levels (
            id              BIGSERIAL PRIMARY KEY,
            profile_id      VARCHAR(64) REFERENCES profiles(id) ON DELETE CASCADE,
            agent_name      TEXT NOT NULL,
            action_kind     TEXT NOT NULL,
            level           INT NOT NULL DEFAULT 1,
            rationale       TEXT,
            updated_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (profile_id, agent_name, action_kind)
        );
        """
    )

    # -- Trust decisions (plan §4.6) ------------------------------------
    op.execute(
        """
        CREATE TABLE trust_decisions (
            id                    BIGSERIAL PRIMARY KEY,
            action_id             UUID REFERENCES gateway_actions(id) ON DELETE CASCADE,
            trust_level_snapshot  INT NOT NULL,
            decision              TEXT NOT NULL,
            reason                TEXT,
            decided_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_trust_decisions_action "
        "ON trust_decisions (action_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trust_decisions CASCADE;")
    op.execute("DROP TABLE IF EXISTS trust_levels CASCADE;")
    op.execute("DROP TABLE IF EXISTS gateway_actions CASCADE;")
    op.execute("DROP TABLE IF EXISTS rl_outcome_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS rl_agent_drafts CASCADE;")
