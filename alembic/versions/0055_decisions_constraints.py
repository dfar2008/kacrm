"""Decisions feature: CHECK constraint + briefing cache uniqueness.

Revision ID: 0055
Revises: 0054
Create Date: 2026-05-10
"""
from __future__ import annotations
from alembic import op


revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 决策消息（kind='copilot_message' AND source LIKE 'decision_mode:%'）
    # 必须 subjects 为 NULL 或空数组——结构上拒绝挂在业务记录上。
    op.execute(
        """
        ALTER TABLE interactions
          ADD CONSTRAINT decision_no_subject_link CHECK (
            NOT (kind = 'copilot_message' AND source LIKE 'decision_mode:%')
            OR (subjects IS NULL OR subjects = '[]'::jsonb)
          )
        """
    )
    # briefing 缓存每经理每天唯一。
    # Use a trigger-based approach to enforce per-day uniqueness.
    # First create the function that checks daily uniqueness.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_briefing_cache_daily_unique()
        RETURNS TRIGGER AS $$
        DECLARE
            user_id_text TEXT;
            check_date DATE;
            existing_count INT;
        BEGIN
            IF NEW.kind = 'decision_briefing_cache' AND NEW.actors IS NOT NULL THEN
                user_id_text := NEW.actors->0->>'id';
                IF user_id_text IS NOT NULL THEN
                    check_date := (NEW.occurred_at AT TIME ZONE 'UTC')::date;
                    SELECT COUNT(*) INTO existing_count
                    FROM interactions
                    WHERE kind = 'decision_briefing_cache'
                      AND (actors->0->>'id') = user_id_text
                      AND (occurred_at AT TIME ZONE 'UTC')::date = check_date
                      AND id != COALESCE(NEW.id, '00000000-0000-0000-0000-000000000000');

                    IF existing_count > 0 THEN
                        RAISE EXCEPTION 'uq_briefing_cache_per_day: briefing cache for user % already exists on %',
                            user_id_text, check_date;
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_briefing_cache_daily_unique
        BEFORE INSERT OR UPDATE ON interactions
        FOR EACH ROW
        EXECUTE FUNCTION check_briefing_cache_daily_unique();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_briefing_cache_daily_unique ON interactions")
    op.execute("DROP FUNCTION IF EXISTS check_briefing_cache_daily_unique()")
    op.execute(
        "ALTER TABLE interactions DROP CONSTRAINT IF EXISTS decision_no_subject_link"
    )
