"""Prune orphan entries from ``module_fields.field_config.option_labels``.

The invariant is: every key in ``option_labels`` must also appear in the
field's ``options`` CSV. Until now the ``delete_field_option`` route
removed the value from ``options`` but left its label entry behind, so
each delete accreted an orphan. dev DB had 3 such orphans on
``potentials.milestone`` (``important_probe``, ``test``,
``long_value_for_overflow_test_abc_xyz``) from earlier UI/migration
probes.

This migration walks every ``module_fields`` row and rebuilds
``option_labels`` to only contain keys that are still in ``options``.
Idempotent. Forward-only-friendly (no downgrade — recovering an orphan
label adds no value).

Revision ID: 0080
Revises: 0079
Create Date: 2026-05-26
"""
from __future__ import annotations

from alembic import op


revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rewrite option_labels to only contain entries whose key is in the
    # comma-split options CSV. Done in pure SQL so we don't pay a row-
    # per-field round trip; jsonb_object_agg + WHERE-against-string_to_array
    # is enough.
    op.execute(
        """
        UPDATE module_fields mf
           SET field_config = jsonb_set(
                   field_config::jsonb,
                   '{option_labels}',
                   COALESCE((
                       SELECT jsonb_object_agg(k, v)
                         FROM jsonb_each((field_config::jsonb -> 'option_labels')::jsonb) AS o(k, v)
                        WHERE k = ANY (string_to_array(mf.options, ','))
                   ), '{}'::jsonb),
                   false
               )
         WHERE field_config IS NOT NULL
           AND jsonb_typeof((field_config::jsonb -> 'option_labels')) = 'object'
           AND options IS NOT NULL
           AND EXISTS (
               SELECT 1
                 FROM jsonb_each((field_config::jsonb -> 'option_labels')::jsonb) AS o(k, v)
                WHERE k <> ALL (string_to_array(mf.options, ','))
           )
        """
    )


def downgrade() -> None:
    # No-op: orphan labels weren't doing anything useful in the first place.
    pass
