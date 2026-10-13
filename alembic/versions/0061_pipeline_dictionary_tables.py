"""Pipeline milestones + lost reasons dictionary tables.

Replaces hardcoded ``select`` options (in ``module_fields.options`` and
``hermes/crm/seed_metadata.py``) for ``potentials.milestone`` and
``potentials.lost_reason`` with admin-configurable dictionary tables.
list_query.py already had picklist resolution code referencing these
table names (``sales_pipeline_milestones`` / ``lost_reasons``) — we
use ``pipeline_milestones`` as the canonical name going forward; the
list_query lookups will be pointed at the new name in PR-2.

Design (see docs/2026-05-12-opportunity-pipeline-governance-plan.md):

* ``name`` is the stable internal key. ``potentials.milestone`` will
  store ``name`` (not id), with FK ``ON name`` — PG supports FK on any
  UNIQUE column, and storing name keeps AI tool calls (e.g.
  ``update_record({milestone: 'negotiation'})``) and API shape
  unchanged.
* ``kind ∈ {progressing, won, lost}`` lets code reason about terminal
  states without string-matching against label names.
* ``is_system=TRUE`` rows (the seeded ``won`` / ``lost`` terminal
  anchors) are protected at the admin route layer — DDL only enforces
  "at most one row per terminal kind" via a partial unique index.
* ``probability`` is descriptive UI metadata (0–100). It is **not** used
  for any forecast math; see §1.2 of the plan doc.

Revision ID: 0061
Revises: 0060
Create Date: 2026-05-12
"""
from __future__ import annotations

from alembic import op


revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pipeline_milestones (
            id            BIGSERIAL PRIMARY KEY,
            name          VARCHAR(250) NOT NULL UNIQUE,
            label         VARCHAR(250) NOT NULL,
            kind          VARCHAR(16)  NOT NULL,
            probability   SMALLINT     NOT NULL DEFAULT 0,
            sort_order    INTEGER      NOT NULL DEFAULT 0,
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            is_system     BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT pipeline_milestones_kind_chk
                CHECK (kind IN ('progressing','won','lost')),
            CONSTRAINT pipeline_milestones_prob_chk
                CHECK (probability BETWEEN 0 AND 100)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uniq_pipeline_terminal "
        "ON pipeline_milestones (kind) WHERE kind IN ('won','lost');"
    )
    op.execute(
        "CREATE TRIGGER trg_pipeline_milestones_updated_at "
        "BEFORE UPDATE ON pipeline_milestones "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    op.execute(
        """
        CREATE TABLE lost_reasons (
            id         BIGSERIAL PRIMARY KEY,
            name       VARCHAR(250) NOT NULL UNIQUE,
            label      VARCHAR(250) NOT NULL,
            sort_order INTEGER      NOT NULL DEFAULT 0,
            is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE TRIGGER trg_lost_reasons_updated_at "
        "BEFORE UPDATE ON lost_reasons "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # Seed: 5 default progressing stages + 2 system terminal rows.
    # probability values are conventional UI hints (not forecast math).
    op.execute(
        """
        INSERT INTO pipeline_milestones
          (name, label, kind, probability, sort_order, is_system) VALUES
          ('initial_contact', 'Initial Contact', 'progressing',  10, 10, FALSE),
          ('needs_analysis',  'Needs Analysis',  'progressing',  20, 20, FALSE),
          ('proposal_sent',   'Proposal Sent',   'progressing',  50, 30, FALSE),
          ('negotiation',     'Negotiation',     'progressing',  70, 40, FALSE),
          ('closing',         'Closing',         'progressing',  90, 50, FALSE),
          ('won',             'Won',             'won',         100, 99, TRUE),
          ('lost',            'Lost',            'lost',          0, 99, TRUE);
        """
    )
    op.execute(
        """
        INSERT INTO lost_reasons
          (name, label, sort_order) VALUES
          ('price_too_high',     'Price too high',     10),
          ('lost_to_competitor', 'Lost to competitor', 20),
          ('no_budget',          'No budget',          30),
          ('wrong_timing',       'Wrong timing',       40),
          ('no_decision_maker',  'No decision maker',  50),
          ('other',              'Other',              99);
        """
    )


def downgrade() -> None:
    # PR-2 will add FK from potentials.milestone / lost_reason; that
    # migration's downgrade must drop FK before this downgrade runs.
    op.execute("DROP TRIGGER IF EXISTS trg_lost_reasons_updated_at ON lost_reasons;")
    op.execute("DROP TRIGGER IF EXISTS trg_pipeline_milestones_updated_at ON pipeline_milestones;")
    op.execute("DROP INDEX IF EXISTS uniq_pipeline_terminal;")
    op.execute("DROP TABLE IF EXISTS lost_reasons;")
    op.execute("DROP TABLE IF EXISTS pipeline_milestones;")
