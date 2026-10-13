"""Tear down role×skill permission gating.

We dropped the "admin can assign skills per role" feature: the SPA
"技能" tab in role editor is gone, and the agent always sees every
on-disk skill regardless of role. Cleaning up the data so the
``collections`` table no longer accumulates ghost ``kind='skill'``
rows on every startup.

* Wipe ``role_permissions`` entries pointing at ``kind='skill'``
  modules or at the now-defunct ``admin_skills`` module.
* Delete the ``kind='skill'`` rows themselves — they were
  auto-registered from disk by the deleted ``sync_skill_modules`` and
  no longer have anyone reading them.
* Delete the ``admin_skills`` module row (the "技能管理" admin entry
  in 0016); its single consumer (the skills tab in role editor) is
  gone too.

We deliberately KEEP:

* The ``permissions`` row with ``code='use'`` — still referenced by
  ``chat.use``, ``analytics.use``, ``agents.use``, ``touchpoints.use``
  and other Hermes-native modules.
* The ``collections.kind / description / metadata`` columns and the
  ``collections_kind_idx`` index — still used to group CRM / Hermes /
  Admin in the role editor.

Revision ID: 0085
Revises: 0084
Create Date: 2026-05-26
"""
from alembic import op


revision = "0085"
down_revision = "0084"


def upgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE module_id IN (
             SELECT id FROM collections
              WHERE kind = 'skill' OR name = 'admin_skills'
         )
    """)
    op.execute("DELETE FROM collections WHERE kind = 'skill'")
    op.execute("DELETE FROM collections WHERE name = 'admin_skills'")


def downgrade() -> None:
    # Skill modules came from on-disk scanning by a function we deleted —
    # there's no source-of-truth to repopulate from. ``admin_skills`` we
    # don't bring back either; its UI is gone. Downgrade is a no-op by
    # design; revert by checking out a pre-0085 commit if you truly need
    # the old data.
    pass
