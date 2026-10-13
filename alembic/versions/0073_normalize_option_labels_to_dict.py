"""Normalize any string-shaped field_config.option_labels entries to
the localized dict form ``{"zh-CN": "...", "en-US": "..."}``.

Why
---
The picklist rename endpoint (``PATCH .../options/{value}``) in its
initial shipment overwrote ``option_labels[value]`` with a bare string,
which collapsed the multi-locale dict structure that the rest of the
codebase (and the SPA's localization layer) expects. After the
endpoint was fixed to keep the dict shape, existing rows that had
already been collapsed need a one-shot normaliser.

What
----
For every ``module_fields`` row, walk ``field_config.option_labels`` and
replace any entry whose JSON type is ``string`` with
``{"zh-CN": <original-string>}``. zh-CN is the SPA default locale, and
matching it preserves "what admin saw" without inventing translations
they never typed. en-US slots are left untouched so re-seed can fill
them later.

The migration is idempotent — re-running it after every entry is
already dict-shaped is a no-op.

Revision ID: 0073
Revises: 0072
Create Date: 2026-05-25
"""
from __future__ import annotations

from alembic import op


revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The query walks every (field_id, key, value) triple where the
    # value is a JSON string, builds the dict form, and writes it back.
    # Done as one SQL pass with jsonb_object_agg so each field's
    # option_labels is rewritten atomically.
    op.execute(
        """
        UPDATE module_fields mf
           SET field_config = jsonb_set(
                   field_config::jsonb,
                   '{option_labels}',
                   (
                       SELECT jsonb_object_agg(
                           k,
                           CASE
                               WHEN jsonb_typeof(v) = 'string'
                                   THEN jsonb_build_object('zh-CN', v #>> '{}')
                               ELSE v
                           END
                       )
                       FROM jsonb_each(
                           (field_config::jsonb -> 'option_labels')::jsonb
                       ) AS o(k, v)
                   ),
                   false
               )
         WHERE field_config IS NOT NULL
           AND jsonb_typeof((field_config::jsonb -> 'option_labels')) = 'object'
           AND EXISTS (
               SELECT 1
                 FROM jsonb_each((field_config::jsonb -> 'option_labels')::jsonb) AS o(k, v)
                WHERE jsonb_typeof(v) = 'string'
           )
        """
    )


def downgrade() -> None:
    # No-op: collapsing dicts back to strings would lose translations,
    # which is worse than the original shape mismatch we're fixing.
    pass
