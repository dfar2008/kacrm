"""Add ``validity_end`` to quotes.

A quote's validity window is a real business concept (reps and customers
both need to know "this offer expires on 2026-05-01"). The original kacrm
port omitted it, so the record-insight scanner (``insights/scanners/
quotes.py``) had to fall back to a coarse ``quote_stale`` signal based on
``created_at`` age. This migration gives us the real field so we can
surface ``expiring_soon`` / ``expired`` hints.

Nullable on purpose — legacy quotes have no validity window and shouldn't
auto-expire.

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-18
"""
from alembic import op

revision = "0019"
down_revision = "0018"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE quotes
        ADD COLUMN IF NOT EXISTS validity_end DATE
    """)
    # Index only needed if we end up with a "expiring soon" report across
    # the whole table. Cheap now, saves a migration later.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_quotes_validity_end
        ON quotes (validity_end)
        WHERE deleted = false AND validity_end IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_quotes_validity_end")
    op.execute("ALTER TABLE quotes DROP COLUMN IF EXISTS validity_end")
