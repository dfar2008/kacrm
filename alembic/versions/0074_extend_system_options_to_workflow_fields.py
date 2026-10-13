"""Extend system_options lock to receivables / quotes / salesorders / tasks / leads.

Why
---
0072 marked ``potentials.milestone.{won,lost}`` as ``system_options`` —
the marker tells the SPA field-builder to render a 🔒 icon and the
schema-edit backend to reject delete/rename on those picklist values.
Code (scanners, constants) refers to those literals so admin-deleting
them would silently break reports.

The same hazard exists on five other status fields that scanners
hard-depend on:

* ``leads.convertedstatus`` — scanner reads ``converted``.
* ``quotes.status`` — scanner reads ``accepted`` / ``rejected``.
* ``salesorders.status`` — scanner reads ``delivered`` /
  ``completed`` / ``cancelled``.
* ``tasks.status`` — scanner reads ``completed``.
* ``receivables.receivablesstatus`` — finance/aging reports depend on
  the full 4-value semantic ladder (``pending`` / ``partial`` /
  ``received`` / ``overdue``).

Scope:

* For ``receivables.receivablesstatus`` we lock **all 4 values** because
  the user explicitly asked for it ("应收款这几个字段都应该是 system
  字段") — finance / aging analytics depend on the full ladder.
* For the other 4 fields we lock **only the scanner-referenced terminal
  values** (the strict "A" interpretation):
    - ``leads.convertedstatus`` → ``converted, failed``(漏斗出口)
    - ``quotes.status`` → ``accepted, rejected``
    - ``salesorders.status`` → ``delivered, completed, cancelled``
    - ``tasks.status`` → ``completed``
  Middle states (e.g. ``draft``, ``not_started``) stay admin-editable;
  if a tenant wants a different labelling for their workflow we don't
  want to block them.

Each terminal value is also mirrored in ``hermes/crm/constants.py``
(e.g. ``TERMINAL_QUOTE_STATUSES``) and read from there by the
``hermes/crm/insights/scanners/*`` modules — single source for the
literal so adding a new terminal only needs one edit.

What
----
Update ``module_fields.field_config`` for each of the 5 fields to add
a ``system_options`` array covering every option in the seed's option
list. Idempotent (overwrites whatever's there).

The seed file ``hermes/crm/seed_metadata.py`` has been updated in the
same commit so freshly-created tenants land in this state via re-seed;
this migration handles existing rows.

Revision ID: 0074
Revises: 0073
Create Date: 2026-05-25
"""
from __future__ import annotations

from alembic import op


revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


# (module_name, field_name, system_option_values_csv)
# Receivables: full 4 values (financial reports depend on all).
# Other 4 fields: scanner-referenced terminals only (see hermes/crm/constants.py).
_TARGETS = [
    ("leads",       "convertedstatus",   "converted,failed"),
    ("quotes",      "status",            "accepted,rejected"),
    ("salesorders", "status",            "delivered,completed,cancelled"),
    ("tasks",       "status",            "completed"),
    ("receivables", "receivablesstatus", "pending,partial,received,overdue"),
]


def upgrade() -> None:
    for module_name, field_name, options_csv in _TARGETS:
        values = [v.strip() for v in options_csv.split(",") if v.strip()]
        # jsonb_build_array(...) needs literal args; build it inline.
        json_array_args = ", ".join(f"'{v}'" for v in values)
        op.execute(
            f"""
            UPDATE module_fields
               SET field_config = COALESCE(field_config, '{{}}'::json)::jsonb
                                  || jsonb_build_object(
                                         'system_options',
                                         jsonb_build_array({json_array_args})
                                     )
             WHERE field_name = '{field_name}'
               AND module_id IN (
                   SELECT id FROM collections WHERE name = '{module_name}'
               )
            """
        )


def downgrade() -> None:
    # Remove the system_options key we just added. Other field_config
    # keys (option_labels, label_i18n, ...) stay untouched.
    for module_name, field_name, _ in _TARGETS:
        op.execute(
            f"""
            UPDATE module_fields
               SET field_config = (field_config::jsonb - 'system_options')::json
             WHERE field_name = '{field_name}'
               AND module_id IN (
                   SELECT id FROM collections WHERE name = '{module_name}'
               )
            """
        )
