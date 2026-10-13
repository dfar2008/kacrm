"""gateway_actions: origin column for channel-closed-loop approval

Adds ``origin JSONB`` to ``gateway_actions`` so the dispatcher can record
which channel produced the action and the channel adapter can deliver
the approval prompt back to that same channel (Charter §1 渠道闭环).

Shape of the JSON payload (per-channel free-form, but ``channel`` is
required):

    {
      "channel": "feishu" | "wecom_ilink" | "discord" | "telegram"
                | "slack" | "dingtalk" | "email" | "webui" | ...,
      "external_user_id":   "...",   # whom to ping for approval
      "external_message_id": "...",  # thread / card to attach to
      "external_chat_id":   "...",   # group / DM context
      "interaction_id":     "<uuid>" # parent interaction, for audit
    }

Existing rows have ``origin = NULL`` and are treated by the approval
guard as "webui" (the only channel that pre-existed before this column).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gateway_actions "
        "ADD COLUMN IF NOT EXISTS origin JSONB"
    )
    # Partial index on the channel discriminator: pending-list filtering
    # by channel scope is the hot read path for the WebUI approval table.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gateway_origin_channel "
        "ON gateway_actions ((origin ->> 'channel')) "
        "WHERE state = 'pending_approval'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gateway_origin_channel")
    op.execute("ALTER TABLE gateway_actions DROP COLUMN IF EXISTS origin")
