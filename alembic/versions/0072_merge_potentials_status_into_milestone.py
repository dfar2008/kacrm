"""Merge potentials.status into milestone; drop pipeline_milestones table.

Why
---
`potentials.status` had been silently drifting since 2026-05-12's pipeline
governance plan: the column was removed (by some out-of-band schema
operation — no migration found in alembic history), but
``hermes/crm/seed_metadata.py`` kept declaring the field, so the SPA
showed it and the agent's ``crm_write`` blew up with
``UndefinedColumn: status``. Today's user feedback also said the two
fields' option sets ("状态" / "里程碑") are conceptually one funnel
and should not be split.

What
----
This migration finalises the merge:

1. Delete ``module_fields`` row for ``potentials.status`` so the SPA and
   ``crm_write`` schema both stop referencing it. (Re-seed won't recreate
   it because we also drop the field from ``seed_metadata.py``.)
2. Rewrite ``potentials.milestone`` options + field_config:
   * options string now carries the full 7-stage funnel including the
     two terminal states ``won`` and ``lost`` (what used to live on
     ``status``).
   * field_config gets a ``system_options=["won","lost"]`` marker so
     frontend renders a 🔒 icon and backend rejects delete/value-rename
     attempts (the values are referenced by literal in
     ``hermes.crm.constants.TERMINAL_MILESTONES``).
   * option_labels gets zh-CN/en-US for ``won`` / ``lost``.
3. Drop the ``pipeline_milestones`` dictionary table. Code that
   referenced it (``hermes/crm/insights/scanners/coach.py``,
   ``hermes_cloud/app/main.py:5097-5288``) has been rewritten to read
   the option order from ``module_fields.options`` directly.

Down-migration is intentionally incomplete: re-creating the dictionary
table from scratch is doable, but recovering the per-row admin edits
(label / sort_order / probability tweaks) the table held is not — those
edits were never replicated to ``module_fields``. Treat this migration
as forward-only in production; the down() supports local dev rollback
of schema only.

Revision ID: 0072
Revises: 0071
Create Date: 2026-05-25
"""
from __future__ import annotations

from alembic import op


revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. Drop the status field row -----------------------------------
    op.execute(
        """
        DELETE FROM module_fields
         WHERE field_name = 'status'
           AND module_id IN (SELECT id FROM collections WHERE name = 'potentials')
        """
    )

    # --- 2. Rewrite milestone options + field_config --------------------
    # options column is overwritten unconditionally by re-seed
    # (no COALESCE in _upsert_module_field), but field_config is
    # COALESCE-protected — so we must explicitly write it here.
    op.execute(
        """
        UPDATE module_fields
           SET options = 'initial_contact,needs_analysis,proposal_sent,negotiation,closing,won,lost',
               field_config = COALESCE(field_config, '{}'::json)::jsonb
                              || jsonb_build_object(
                                   'system_options', jsonb_build_array('won', 'lost'),
                                   'option_labels',
                                   COALESCE(
                                       (field_config->'option_labels')::jsonb,
                                       '{}'::jsonb
                                   ) || jsonb_build_object(
                                       'won', jsonb_build_object(
                                           'zh-CN', '赢单', 'en-US', 'Won'
                                       ),
                                       'lost', jsonb_build_object(
                                           'zh-CN', '输单', 'en-US', 'Lost'
                                       )
                                   )
                               )
         WHERE field_name = 'milestone'
           AND module_id IN (SELECT id FROM collections WHERE name = 'potentials')
        """
    )

    # --- 3. Drop the dictionary table -----------------------------------
    # Cascade in case some downstream object still references it; we
    # already removed the known reference (admin endpoints + coach scanner)
    # in the same commit but CASCADE is the belt-and-suspenders.
    op.execute("DROP TABLE IF EXISTS pipeline_milestones CASCADE")

    # --- 4. Extend schema_changelog op CHECK constraint -----------------
    # The new picklist-option admin endpoints emit two new ops:
    # 'delete_option' (admin removed a picklist value) and
    # 'rename_option_label' (admin retitled an option's display label).
    # Without adding them here the audit-row INSERT inside the endpoints
    # would explode with check_violation.
    op.execute(
        "ALTER TABLE schema_changelog "
        "DROP CONSTRAINT IF EXISTS schema_changelog_op_check"
    )
    op.execute(
        "ALTER TABLE schema_changelog "
        "ADD CONSTRAINT schema_changelog_op_check "
        "CHECK (op = ANY (ARRAY["
        "'add_field', 'update_field', 'hide_field', "
        "'add_option', 'hide_option', 'reorder_fields', "
        "'delete_option', 'rename_option_label'"
        "]::text[]))"
    )


def downgrade() -> None:
    # Schema-only rollback. See module docstring re: data loss.
    op.execute(
        "ALTER TABLE schema_changelog "
        "DROP CONSTRAINT IF EXISTS schema_changelog_op_check"
    )
    op.execute(
        "ALTER TABLE schema_changelog "
        "ADD CONSTRAINT schema_changelog_op_check "
        "CHECK (op = ANY (ARRAY["
        "'add_field', 'update_field', 'hide_field', "
        "'add_option', 'hide_option', 'reorder_fields'"
        "]::text[]))"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_milestones (
            id            BIGSERIAL PRIMARY KEY,
            name          VARCHAR(64) UNIQUE NOT NULL,
            label         VARCHAR(255) NOT NULL,
            kind          VARCHAR(20)  NOT NULL DEFAULT 'progressing',
            probability   INTEGER      NOT NULL DEFAULT 0,
            sort_order    INTEGER      NOT NULL DEFAULT 0,
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            is_system     BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_pipeline_terminal
            ON pipeline_milestones (kind) WHERE kind IN ('won', 'lost')
        """
    )

    # Re-shrink milestone to the legacy 5-stage option set.
    op.execute(
        """
        UPDATE module_fields
           SET options = 'initial_contact,needs_analysis,proposal_sent,negotiation,closing',
               field_config = (field_config::jsonb - 'system_options')::json
         WHERE field_name = 'milestone'
           AND module_id IN (SELECT id FROM collections WHERE name = 'potentials')
        """
    )

    # Re-create the status field row (admin will have to re-edit any
    # custom labels — those were lost when we dropped the column).
    op.execute(
        """
        INSERT INTO module_fields (
            module_id, field_label, field_name, field_type,
            is_required, is_readonly, is_default, is_system,
            is_summary, is_searchable, sort_order, options
        )
        SELECT id, 'Status', 'status', 'select',
               false, false, true, false,
               false, true, 5,
               'new,negotiation,proposal,won,lost'
          FROM collections WHERE name = 'potentials'
        """
    )
