"""Add search_text projection + GIN tsvector index on interactions.

Revision ID: 0027
Revises: 0026
"""

from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE interactions "
        "ADD COLUMN IF NOT EXISTS search_text TEXT"
    )
    op.execute(
        """
        UPDATE interactions
           SET search_text = btrim(
               concat_ws(
                   ' ',
                   coalesce(content->>'summary', ''),
                   coalesce(content->>'text', ''),
                   coalesce(content->>'body', ''),
                   coalesce(content->>'subject', ''),
                   coalesce(content->>'message', '')
               )
           )
         WHERE search_text IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_interactions_search_tsv
            ON interactions
         USING gin (to_tsvector('simple', coalesce(search_text, '')))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interactions_search_tsv")
    op.execute(
        "ALTER TABLE interactions "
        "DROP COLUMN IF EXISTS search_text"
    )
