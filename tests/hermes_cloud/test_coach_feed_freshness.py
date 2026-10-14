"""Execute feed freshness and scope regressions against an isolated PostgreSQL.

Run with HERMES_TESTS_REAL_PG=1 and KACRM_COACH_TEST_DSN pointing to a
scratch database. Each test uses a connection-local temporary table.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import pytest

from hermes.crm.insights.coach_cards import read_coach_proposals, count_unread_coach
from hermes_cloud.app.insights import api as insights_api

pytestmark = pytest.mark.requires_real_pg


class CoachDb:
    def __init__(self, connection):
        self.connection = connection

    def fetchall(self, sql, args):
        return self.connection.execute(sql, args).fetchall()

    def fetchone(self, sql, args):
        return self.connection.execute(sql, args).fetchone()


@pytest.fixture
def coach_db():
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("KACRM_COACH_TEST_DSN")
    if not dsn:
        pytest.skip("KACRM_COACH_TEST_DSN must point to a scratch PostgreSQL")
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute("SET client_encoding TO 'UTF8'")
        connection.execute("""
            CREATE TEMP TABLE gateway_actions (
                id bigint PRIMARY KEY, kind text NOT NULL,
                params jsonb NOT NULL, target jsonb NOT NULL,
                state text NOT NULL, created_at timestamptz NOT NULL
            )
        """)
        yield CoachDb(connection)
        connection.rollback()


def insert_card(db, card_id, *, age_hours=0, module="accounts", user_id=1,
                state="pending_approval"):
    db.connection.execute(
        "INSERT INTO gateway_actions VALUES (%s, 'coach.proposal', %s::jsonb, "
        "%s::jsonb, %s, now() - %s * interval '1 hour')",
        (card_id, json.dumps({"user_id": str(user_id), "summary": f"card {card_id}",
                             "severity": "high"}),
         json.dumps({"module": module, "id": card_id}), state, age_hours),
    )


def test_last_run_from_months_ago_does_not_become_today_feed(coach_db):
    insert_card(coach_db, 1, age_hours=24 * 90)
    assert read_coach_proposals(coach_db, 1) == []


@pytest.mark.parametrize("age_hours", [24, 25])
def test_expired_last_run_does_not_keep_unread_badge(coach_db, age_hours):
    insert_card(coach_db, 1, age_hours=age_hours)
    assert count_unread_coach(coach_db, 1) == {"n": 0, "n_high": 0}


def test_explicit_old_since_cannot_resurrect_expired_cards(coach_db):
    insert_card(coach_db, 1, age_hours=25)
    since = coach_db.connection.execute("SELECT now() - interval '7 days' AS t").fetchone()["t"]
    assert read_coach_proposals(coach_db, 1, since=since) == []


def test_recent_run_is_available_within_24_hours(coach_db):
    insert_card(coach_db, 1, age_hours=23.99)
    assert [c["id"] for c in read_coach_proposals(coach_db, 1)] == ["1"]
    assert count_unread_coach(coach_db, 1) == {"n": 1, "n_high": 1}


def test_latest_run_scope_and_actor_filters_are_preserved(coach_db):
    insert_card(coach_db, 1, age_hours=1)
    insert_card(coach_db, 2, module="accounts")
    insert_card(coach_db, 3, module="potentials")
    insert_card(coach_db, 4, user_id=2)
    insert_card(coach_db, 5, state="rejected")
    assert [c["id"] for c in read_coach_proposals(coach_db, 1, module="accounts")] == ["2"]
    assert count_unread_coach(coach_db, 1, module="accounts") == {"n": 1, "n_high": 1}


def patch_cached_brief(monkeypatch, db):
    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(insights_api, "db_session", session)
    monkeypatch.setattr(insights_api, "_build_daily_feed", lambda *a, **kw: {"outcomes": {}, "counts": {}, "items": []})
    monkeypatch.setattr(insights_api, "_build_today_priority", lambda *a, **kw: [])
    from hermes.crm.insights import ai_noticed, brief_cache
    monkeypatch.setattr(ai_noticed, "compute_ai_noticed", lambda *a, **kw: [])
    monkeypatch.setattr(brief_cache, "get_cached_brief", lambda *a, **kw: {"summary": "cached", "suggestions": []})


@pytest.mark.asyncio
async def test_cached_customer_brief_only_contains_customer_cards(monkeypatch, coach_db):
    insert_card(coach_db, 1, module="accounts")
    insert_card(coach_db, 2, module="potentials")
    patch_cached_brief(monkeypatch, coach_db)
    result = await insights_api._list_brief_inner(1, "accounts", {"v": ""})
    assert result["source"] == "llm_cached"
    assert [c["module"] for c in result["coach_cards"]] == ["accounts"]


@pytest.mark.asyncio
async def test_cached_global_brief_keeps_current_cross_module_cards(monkeypatch, coach_db):
    insert_card(coach_db, 1, module="accounts")
    insert_card(coach_db, 2, module="potentials")
    patch_cached_brief(monkeypatch, coach_db)
    result = await insights_api._list_brief_inner(1, None, {"v": ""})
    assert {c["module"] for c in result["coach_cards"]} == {"accounts", "potentials"}


@pytest.mark.asyncio
async def test_cached_brief_cannot_reintroduce_expired_cards(monkeypatch, coach_db):
    insert_card(coach_db, 1, age_hours=25)
    patch_cached_brief(monkeypatch, coach_db)
    result = await insights_api._list_brief_inner(1, "accounts", {"v": ""})
    assert result["coach_cards"] == []
