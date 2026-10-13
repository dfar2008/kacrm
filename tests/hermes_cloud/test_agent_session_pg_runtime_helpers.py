"""Exercise shared runtime helpers with the cloud facade dispatch, without DDL."""
import json
from contextlib import contextmanager
from copy import deepcopy

import pytest

from hermes_cloud.app.agent_session_pg import PgSessionDB


class _TranscriptTransport:
    """In-memory SQL transport; runtime, flush, codecs and reload stay real."""
    def __init__(self):
        self.rows = []
        self.callbacks = []

    @contextmanager
    def transaction(self):
        before = deepcopy(self.rows)
        try:
            yield
        except BaseException:
            self.rows = before
            self.callbacks.clear()
            raise
        else:
            callbacks, self.callbacks = self.callbacks, []
            for callback in callbacks:
                callback()

    def after_commit(self, callback):
        self.callbacks.append(callback)

    def _maybe_commit(self, flag):
        pass

    def execute(self, sql, params=None):
        pass  # session counters are covered by the real PG contracts

    def fetchone(self, sql, params):
        if "INSERT INTO agent_messages" in sql:
            columns = sql.split("VALUES")[0].split("(", 1)[1].rsplit(")", 1)[0]
            names = [column.strip() for column in columns.split(",")]
            assert len(names) == len(params)
            row = dict(zip(names, params))
            for name in ("tool_calls", "reasoning_details", "codex_reasoning_items", "codex_message_items",
                         "display_metadata", "turn_meta", "absorbed_message_uids", "tool_call_uids"):
                if row.get(name) is not None:
                    row[name] = json.loads(row[name])
            row["id"] = len(self.rows) + 1
            self.rows.append(row)
            return {"id": row["id"]}
        if "SELECT user_id" in sql:
            return {"user_id": None}
        raise AssertionError(sql)

    def fetchall(self, sql, params):
        assert "FROM agent_messages" in sql
        return deepcopy([row for row in self.rows if row["session_id"] == params[0]])


def test_active_flush_current_wire_reload_with_cloud_facade(monkeypatch):
    import hermes_state
    from agent.agent_runtime_helpers import repair_message_sequence
    from agent.context_compressor import _DB_PERSISTED_MARKER
    from agent.session_persistence import SessionPersistenceMixin

    monkeypatch.setattr(hermes_state, "SessionDB", PgSessionDB)
    transport = _TranscriptTransport()
    @contextmanager
    def session():
        yield transport
    monkeypatch.setattr("hermes.crm.db.db_session", session)
    monkeypatch.setattr("hermes_cloud.app.agent_session_pg._emit_agent_message_interaction", lambda **kw: None)

    class Agent(SessionPersistenceMixin):
        _session_db = PgSessionDB()
        _session_db_created = True
        session_id = "active"
        _persist_user_message_idx = 2
        _persist_user_message_override = "clean current turn"

    agent = Agent()
    image_parts = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
    messages = [
        {"role": "user", "content": "\x00json:" + json.dumps(image_parts), "message_uid": "image"},
        {"role": "assistant", "content": "ready", "message_uid": "reply"},
        {"role": "user", "content": "enriched current wire bytes", "message_uid": "current"},
    ]
    assert repair_message_sequence(agent, messages) == 0
    assert messages[0]["content"] == image_parts
    assert agent._flush_messages_to_session_db(messages) is True
    assert all(message[_DB_PERSISTED_MARKER] for message in messages)
    assert messages[2]["content"] == "enriched current wire bytes"
    assert transport.rows[2]["content"] == "clean current turn"
    loaded = agent._session_db.get_messages_as_conversation("active", include_row_ids=True)
    assert loaded[2]["content"] == "clean current turn"
    assert loaded[2]["api_content"] == "enriched current wire bytes"
    assert loaded[2]["message_uid"] == "current"
    assert repair_message_sequence(agent, loaded) == 0
    assert agent._flush_messages_to_session_db(messages) is True
    assert len(transport.rows) == 3


def test_reserved_codec_prefix_and_surrogates_roundtrip():
    literal = PgSessionDB._CONTENT_JSON_PREFIX + '[{"type":"text","text":"literal"}]'
    assert PgSessionDB._decode_content(PgSessionDB._encode_content(literal)) == literal
    assert PgSessionDB._decode_content(PgSessionDB._encode_content("bad\ud800text")) == "bad�text"
    assert PgSessionDB._decode_display_metadata('{"kind": "hidden"}') == {"kind": "hidden"}


def test_migration_acquires_session_then_message_locks_before_ddl(monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0115_agent_message_identity_routes.py"
    spec = importlib.util.spec_from_file_location("migration_lock_contract", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    assert statements[0] == "SET LOCAL lock_timeout = '30s'"
    assert statements[1] == "LOCK TABLE agent_sessions, agent_messages IN ACCESS EXCLUSIVE MODE"
    assert statements[2].startswith("ALTER TABLE")


def test_cloud_repair_does_not_merge_malformed_legacy_sentinel(monkeypatch):
    import hermes_state
    from agent.agent_runtime_helpers import repair_message_sequence
    monkeypatch.setattr(hermes_state, "SessionDB", PgSessionDB)
    messages = [{"role": "user", "content": "\x00json:malformed"},
                {"role": "user", "content": "next turn"}]
    assert repair_message_sequence(None, messages) == 0
    assert len(messages) == 2
    assert messages[0]["content"] == "\x00json:malformed"
