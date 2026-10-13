"""specialist_delegation_acl: array CHECK + edges table COMMENT

Code-quality hardening from the Task 1 review:

(a) ``specialist_delegation_acl.allowed_action_kinds`` is JSONB. The
    seed loader writes a list-of-strings; the toolset_builder reads
    it as a list. A typo / wrong type elsewhere (someone INSERTing an
    object or a bare string) silently lands in the DB and only
    surfaces at runtime as a confusing iteration error. CHECK
    constraint enforces the array-typed invariant at write time.

(b) ``specialist_delegation_edges.parent_draft_id`` /
    ``child_draft_id`` deliberately have NO foreign key to
    ``gateway_actions.id`` — edges may be opened before the action
    commits (cloud_resolver records the open at child build, the
    action UUID arrives later) or for children that never produced
    an action (``edge_outcome='no_action'``). A schema comment
    documents the design choice so future migrations don't add the
    FK reflexively.

Charter: §一原语 ④ Agent Pool + ⑥ Feedback Collector. See
docs/2026-04-26-v23-specialist-runtime-design.md §2.5.B / §2.5.D.

Revision ID: 0039
Revises: 0038
"""

from alembic import op


revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE specialist_delegation_acl "
        "ADD CONSTRAINT specialist_delegation_acl_allowed_action_kinds_is_array "
        "CHECK (jsonb_typeof(allowed_action_kinds) = 'array')"
    )
    op.execute(
        "COMMENT ON COLUMN specialist_delegation_edges.parent_draft_id IS "
        "'No FK to gateway_actions.id by design — edges open before the "
        "child action commits, and ''no_action'' children never produce "
        "a draft. See docs/2026-04-26-v23-specialist-runtime-design.md §2.5.D.'"
    )
    op.execute(
        "COMMENT ON COLUMN specialist_delegation_edges.child_draft_id IS "
        "'No FK to gateway_actions.id by design — see parent_draft_id "
        "comment.'"
    )


def downgrade() -> None:
    op.execute(
        "COMMENT ON COLUMN specialist_delegation_edges.child_draft_id IS NULL"
    )
    op.execute(
        "COMMENT ON COLUMN specialist_delegation_edges.parent_draft_id IS NULL"
    )
    op.execute(
        "ALTER TABLE specialist_delegation_acl "
        "DROP CONSTRAINT IF EXISTS "
        "specialist_delegation_acl_allowed_action_kinds_is_array"
    )
