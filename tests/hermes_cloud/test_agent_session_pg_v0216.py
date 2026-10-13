"""Runtime contracts on a disposable PG schema (never the configured CRM DB).

Run with HERMES_PG_CONTRACT_TEST_DSN pointing at a disposable PostgreSQL.
"""
import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.requires_real_pg

from hermes_cloud.app.agent_session_pg import PgSessionDB


@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("HERMES_PG_CONTRACT_TEST_DSN")
    if not dsn:
        pytest.skip("requires explicit disposable HERMES_PG_CONTRACT_TEST_DSN")
    import psycopg
    from psycopg.rows import dict_row
    from hermes.crm.db import DbWrapper
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa

    schema = "hermes_contract_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
    root = Path(__file__).resolve().parents[2]
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'SET search_path TO "{schema}"')
            ops = Operations(MigrationContext.configure(conn))
            for revision in ("0031", "0033", "0034", "0048", "0110", "0112", "0114", "0115"):
                path = next((root / "alembic/versions").glob(revision + "_*.py"))
                spec = importlib.util.spec_from_file_location("migration_" + revision, path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.op = ops
                if revision == "0115":
                    ops.execute("INSERT INTO agent_sessions (id, source, started_at) VALUES ('legacy', 'cron', 50)")
                    ops.execute("INSERT INTO agent_messages (session_id, role, content, timestamp, api_content, display_kind, duration_ms, turn_meta) VALUES ('legacy', 'assistant', 'old', 51, 'exact old bytes', 'hidden', 0, '{}'::jsonb)")
                mod.upgrade()
        @contextmanager
        def session():
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                conn.execute(f'SET search_path TO "{schema}"')
                yield DbWrapper(conn)
        monkeypatch.setattr("hermes.crm.db.db_session", session)
        events = []
        monkeypatch.setattr("hermes_cloud.app.agent_session_pg._emit_agent_message_interaction", lambda **kw: events.append(kw))
        db = PgSessionDB()
        db.create_session("s", "cron", model="initial")
        yield db, session, events
    finally:
        engine.dispose()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_identity_batch_reload_replace_and_timing(pg):
    db, session, events = pg
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "call"}],
         "tool_calls": [{"id": "same", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
         "message_uid": "message-a", "_absorbed_message_uids": ["prior", "prior"],
         "_tool_call_uids": {"same": ["call-a", "call-b"]}, "duration_ms": 0,
         "turn_meta": {"total_ms": 0}, "api_content": "exact\nbytes", "display_kind": "hidden",
         "display_metadata": {"why": "compaction"}, "timestamp": 123},
        {"role": "tool", "content": "result", "tool_call_id": "same", "message_uid": "message-b",
         "_tool_call_uid": "call-b", "effect_disposition": "executed", "observed": True,
         "platform_message_id": "external", "timestamp": 124},
    ]
    assert db.append_messages_batch("s", messages) == 2
    assert len(events) == 2
    restored = db.get_messages_as_conversation("s")
    assert restored[0]["message_uid"] == "message-a"
    assert restored[0]["_absorbed_message_uids"] == ["prior"]
    assert restored[0]["_tool_call_uids"] == {"same": ["call-a", "call-b"]}
    assert restored[1]["_tool_call_uid"] == "call-b"
    assert restored[1]["effect_disposition"] == "executed"
    db.replace_messages("s", restored)
    assert db.get_messages_as_conversation("s") == restored
    rows = db.get_messages("s")
    assert rows[0]["duration_ms"] == 0
    assert rows[0]["turn_meta"] == {"total_ms": 0}
    assert rows[0]["display_kind"] == "hidden"
    assert rows[0]["api_content"] == "exact\nbytes"
    assert rows[1]["observed"] is True
    assert db.get_message_role("s", rows[1]["id"]) == "tool"
    assert db.get_message_role("other", rows[1]["id"]) is None


def test_batch_failure_rolls_back_rows_counters_and_projection(pg):
    db, session, events = pg
    import psycopg
    with pytest.raises(psycopg.errors.NotNullViolation):
        db.append_messages_batch("s", [{"role": "user", "content": "first"}, {"role": None}])
    assert db.get_messages("s") == []
    assert db.get_session("s")["message_count"] == 0
    assert events == []


def test_minted_identity_and_tool_pairing(pg):
    db, session, events = pg
    msgs = [{"role": "assistant", "tool_calls": [{"id": "provider-id"}]},
            {"role": "tool", "tool_call_id": "provider-id", "content": "ok"}]
    db.append_messages_batch("s", msgs)
    loaded = db.get_messages_as_conversation("s")
    assert loaded[0]["message_uid"] == msgs[0]["message_uid"]
    assert msgs[0]["_row_id"] == db.get_messages("s")[0]["id"]
    assert loaded[0]["timestamp"] == msgs[0]["timestamp"]
    assert loaded[0]["_tool_call_uids"]["provider-id"] == loaded[1]["_tool_call_uid"]


def test_usage_recent_route_excludes_aux_and_creates_source(pg):
    db, session, events = pg
    db.update_token_counts("fresh", input_tokens=10, model="a", billing_provider="p", api_call_count=1, source="cron")
    db.queue_token_counts("fresh", input_tokens=20, model="b", billing_provider="q", billing_base_url="url", api_call_count=1, source="cron")
    db.update_token_counts("fresh", input_tokens=30, model="aux", billing_provider="r", api_call_count=1, task="voice_chat")
    route = db.get_recent_session_model_route("fresh")
    assert route["model"] == "b"
    assert route["billing_provider"] == "q"
    assert route["billing_base_url"] == "url"
    assert db.get_session("fresh")["input_tokens"] == 60
    assert db.get_session("fresh")["source"] == "cron"
    assert db.get_recent_session_model_route("s") is None
    db.update_token_counts("fresh", model="a", billing_provider="p", api_call_count=5)
    assert db.get_dominant_session_model_route("fresh")["model"] == "a"
    assert db.get_dominant_session_model_route("fresh")["api_call_count"] == 6


def test_cwd_generation_and_git_reset(pg):
    db, session, events = pg
    assert db.update_session_cwd("s", "/a", "main", "/repo") == 1
    assert db.update_session_cwd("s", "/a") == 2
    assert db.get_session("s")["git_branch"] == "main"
    assert db.update_session_cwd("s", "/b") == 3
    assert db.get_session("s")["git_branch"] is None
    assert db.update_session_cwd("absent", "/a") is None


def test_search_session_start_bounds_lexical_and_semantic_fallback(pg, monkeypatch):
    db, session, events = pg
    db.create_session("later", "cron")
    with session() as sql:
        sql.execute("UPDATE agent_sessions SET started_at = CASE WHEN id = 's' THEN 100 ELSE 200 END")
    db.append_message("s", "user", "needle searchable", timestamp=999)
    db.append_message("later", "user", "needle searchable", timestamp=1)
    assert [r["session_id"] for r in db.search_messages("needle", after_ts=100, before_ts=200)] == ["s"]
    monkeypatch.setattr(db, "_search_by_embedding", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("offline")))
    assert [r["session_id"] for r in db.search_messages("needle searchable", after_ts=200, before_ts=201)] == ["later"]


def test_replacement_failure_preserves_transcript_and_counters(pg):
    db, session, events = pg
    import psycopg
    db.append_message("s", "user", "keep me", message_uid="keep")
    before = db.get_messages("s")
    with pytest.raises(psycopg.errors.NotNullViolation):
        db.replace_messages("s", [{"role": "assistant", "content": "partial"}, {"role": None}])
    assert db.get_messages("s") == before
    assert db.get_session("s")["message_count"] == 1


def test_aux_usage_never_backfills_main_route_and_row_id_reload(pg):
    db, session, events = pg
    db.update_token_counts("aux-first", input_tokens=5, model="voice", billing_provider="voice-p", task="voice_chat")
    assert db.get_session("aux-first")["model"] is None
    assert db.get_session("aux-first")["billing_provider"] is None
    assert db.get_recent_session_model_route("aux-first") is None
    row_id = db.append_message("s", "user", "owned")
    assert db.get_messages_as_conversation("s", include_row_ids=True)[0]["_row_id"] == row_id


def test_upstream_live_flush_preserves_identity_and_effect(pg):
    from types import SimpleNamespace
    from agent.session_persistence import _db_flush_row, _db_flush_write
    db, session, events = pg
    agent = SimpleNamespace(_session_db=db, session_id="s", _last_api_duration_ms=12)
    messages = [{"role": "assistant", "content": "done", "message_uid": "live-uid",
                 "_absorbed_message_uids": ["merged-uid"], "_tool_call_uids": {"id": "tool-uid"},
                 "tool_calls": [{"id": "id"}], "effect_disposition": "executed",
                 "timestamp": 123.0, "display_kind": "hidden"}]
    rows = [_db_flush_row(agent, messages[0], False)]
    _db_flush_write(agent, rows, messages, messages)
    restored = db.get_messages_as_conversation("s")[0]
    assert restored["message_uid"] == "live-uid"
    assert restored["_absorbed_message_uids"] == ["merged-uid"]
    assert restored["_tool_call_uids"] == {"id": "tool-uid"}
    assert restored["effect_disposition"] == "executed"
    assert restored["duration_ms"] == 12
    assert restored["display_kind"] == "hidden"


def test_migration_backfills_legacy_identity_without_changing_payload(pg):
    db, session, events = pg
    first = db.get_messages_as_conversation("legacy")[0]
    assert isinstance(first["message_uid"], str) and first["message_uid"]
    assert db.get_messages_as_conversation("legacy")[0]["message_uid"] == first["message_uid"]
    assert first["content"] == "old"
    assert first["api_content"] == "exact old bytes"
    assert first["display_kind"] == "hidden"
    assert first["duration_ms"] == 0
    assert first["turn_meta"] == {}


def test_direct_append_accepts_export_identity_shapes(pg):
    db, session, events = pg
    db.append_message("s", "assistant", "payload", message_uid="exported",
                      absorbed_message_uids='["a", "a", 5]',
                      tool_call_uids='{"id": ["one", "two"], "bad": 4}',
                      tool_call_uid="result", turn_meta={})
    msg = db.get_messages_as_conversation("s")[0]
    assert msg["message_uid"] == "exported"
    assert msg["_absorbed_message_uids"] == ["a"]
    assert msg["_tool_call_uids"] == {"id": ["one", "two"]}
    assert msg["_tool_call_uid"] == "result"
    assert msg["turn_meta"] == {}


def test_active_flush_current_wire_reload_on_pg(pg, monkeypatch):
    import json
    import hermes_state
    from agent.agent_runtime_helpers import repair_message_sequence
    from agent.context_compressor import _DB_PERSISTED_MARKER
    from agent.session_persistence import SessionPersistenceMixin

    db, session, events = pg
    monkeypatch.setattr(hermes_state, "SessionDB", PgSessionDB)

    class Agent(SessionPersistenceMixin):
        _session_db = db
        _session_db_created = True
        session_id = "s"
        _persist_user_message_idx = 2
        _persist_user_message_override = "clean current turn"

    agent = Agent()
    parts = [{"type": "text", "text": "look"},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
    messages = [{"role": "user", "content": "\x00json:" + json.dumps(parts)},
                {"role": "assistant", "content": "ready"},
                {"role": "user", "content": "enriched current wire bytes", "message_uid": "current"}]
    assert repair_message_sequence(agent, messages) == 0
    assert messages[0]["content"] == parts
    assert agent._flush_messages_to_session_db(messages) is True
    assert all(message[_DB_PERSISTED_MARKER] for message in messages)
    assert messages[2]["content"] == "enriched current wire bytes"
    restored = db.get_messages_as_conversation("s", include_row_ids=True)
    assert restored[2]["content"] == "clean current turn"
    assert restored[2]["api_content"] == "enriched current wire bytes"
    assert restored[2]["message_uid"] == "current"
    assert repair_message_sequence(agent, restored) == 0
    assert agent._flush_messages_to_session_db(messages) is True
    assert len(db.get_messages("s")) == 3
