"""Tests for trust engine auto-adjustment logic (plan §9)."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Pure function tests — evaluate_trust_level with mock stats
# ---------------------------------------------------------------------------


def test_evaluate_no_change_insufficient_data():
    """With zero actions, level should not change."""
    from hermes.trust.auto_adjust import (
        PROMOTE_MIN_ACTIONS,
        PROMOTE_MIN_DAYS,
        evaluate_trust_level,
    )

    # Mock a db that returns empty stats
    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 0}
            return {
                "total": 5,  # Far below threshold
                "executed": 5,
                "reverted": 0,
                "rejected": 0,
                "first_action_days_ago": 10,  # Too few days
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 1, MockDb())
    assert new_level == 1
    assert "no change" in reason


def test_evaluate_promote():
    """Meets all promotion criteria — should promote by 1."""
    from hermes.trust.auto_adjust import evaluate_trust_level

    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 0}
            return {
                "total": 150,
                "executed": 140,
                "reverted": 0,
                "rejected": 10,
                "first_action_days_ago": 35.0,
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 2, MockDb())
    assert new_level == 3
    assert "promoted" in reason


def test_evaluate_demote():
    """2+ reverts in 7 days — should demote by 1."""
    from hermes.trust.auto_adjust import evaluate_trust_level

    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 3}  # 3 reverts — above threshold
            return {
                "total": 50,
                "executed": 45,
                "reverted": 5,
                "rejected": 0,
                "first_action_days_ago": 20,
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 3, MockDb())
    assert new_level == 2
    assert "demoted" in reason
    assert "3 reverts" in reason


def test_evaluate_demote_takes_priority_over_promote():
    """Even if promotion criteria are met, demotion wins (safety first)."""
    from hermes.trust.auto_adjust import evaluate_trust_level

    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 2}  # Meets demotion threshold
            return {
                "total": 200,
                "executed": 190,
                "reverted": 2,
                "rejected": 8,
                "first_action_days_ago": 60,
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 3, MockDb())
    assert new_level == 2  # Demoted, not promoted
    assert "demoted" in reason


def test_evaluate_max_level_no_promote():
    """Already at L5 — cannot promote further."""
    from hermes.trust.auto_adjust import evaluate_trust_level

    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 0}
            return {
                "total": 500,
                "executed": 490,
                "reverted": 0,
                "rejected": 10,
                "first_action_days_ago": 90,
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 5, MockDb())
    assert new_level == 5
    assert "max level" in reason


def test_evaluate_min_level_no_demote():
    """Already at L0 — cannot demote further."""
    from hermes.trust.auto_adjust import evaluate_trust_level

    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 5}
            return {
                "total": 10,
                "executed": 5,
                "reverted": 5,
                "rejected": 0,
                "first_action_days_ago": 10,
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 0, MockDb())
    assert new_level == 0  # Can't go below 0


def test_evaluate_adoption_rate_too_low():
    """High action count but low adoption — should not promote."""
    from hermes.trust.auto_adjust import evaluate_trust_level

    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 0}
            return {
                "total": 200,
                "executed": 100,  # 50% adoption — below 85%
                "reverted": 0,
                "rejected": 100,
                "first_action_days_ago": 45,
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 2, MockDb())
    assert new_level == 2  # No change
    assert "no change" in reason


def test_evaluate_has_reverts_no_promote():
    """Even 1 revert in window blocks promotion."""
    from hermes.trust.auto_adjust import evaluate_trust_level

    class MockDb:
        def fetchone(self, sql, params=None):
            if "reverted_at" in sql:
                return {"cnt": 0}  # No recent reverts for demotion
            return {
                "total": 200,
                "executed": 190,
                "reverted": 1,  # 1 revert blocks promotion
                "rejected": 9,
                "first_action_days_ago": 45,
            }

    new_level, reason = evaluate_trust_level("p1", "agent", "kind", 2, MockDb())
    assert new_level == 2  # No promotion due to revert
    assert "no change" in reason


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


def test_constants_match_plan():
    """Ensure threshold constants match plan §9 values."""
    from hermes.trust.auto_adjust import (
        DEMOTE_REVERT_THRESHOLD,
        DEMOTE_REVERT_WINDOW_DAYS,
        PROMOTE_MIN_ACTIONS,
        PROMOTE_MIN_ADOPTION_RATE,
        PROMOTE_MIN_DAYS,
    )

    assert PROMOTE_MIN_DAYS == 30
    assert PROMOTE_MIN_ACTIONS == 100
    assert PROMOTE_MIN_ADOPTION_RATE == 0.85
    assert DEMOTE_REVERT_WINDOW_DAYS == 7
    assert DEMOTE_REVERT_THRESHOLD == 2
