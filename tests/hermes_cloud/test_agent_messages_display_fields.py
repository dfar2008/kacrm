"""hermes v0.20 per-message display fields on the PG/cloud path.

``run_agent``'s turn-boundary batch flush stamps every row with
``api_content`` / ``display_kind`` / ``display_metadata`` (SessionState
refactor). ``display_kind='hidden'`` marks compaction-summary carrier rows —
upstream #80622: if the flag is lost, a reloaded summary carrier re-occupies
the active user slot and renders as a real user message. ``api_content`` is
the byte-fidelity "exact bytes sent to the API" sidecar for
prompt-cache-stable replay.

PgSessionDB used to strip these in ``append_messages_batch`` and drop
non-None values through ``append_message``'s ``**kwargs`` drift warning, so
every post-compaction session reload regressed on cloud. These tests pin:

* write path: the three fields land in the INSERT (append_message,
  append_messages_batch pass-through, replace_messages);
* read path: get_messages / get_messages_as_conversation surface them the
  same way sqlite's projection does;
* interactions fan-out: ``display_kind='hidden'`` rows are NOT emitted (the
  SPA/search projection has no client-side filter);
* real-PG round-trip (``requires_real_pg``).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _columns_to_params(sql: str, params: tuple) -> dict:
    """Map the column list of an INSERT statement onto its bind params."""
    cols_blob = sql.split("VALUES")[0]
    cols_blob = cols_blob[cols_blob.index("(") + 1: cols_blob.rindex(")")]
    cols = [c.strip() for c in cols_blob.split(",")]
    assert len(cols) == len(params), (
        f"column/param count mismatch: {len(cols)} columns vs "
        f"{len(params)} params"
    )
    return dict(zip(cols, params))


class _CaptureDb:
    """db_session stand-in that captures agent_messages INSERTs."""

    def __init__(self) -> None:
        self.inserts: list[dict] = []
        self._callbacks = []

    @contextmanager
    def transaction(self):
        try:
            yield
        except BaseException:
            self._callbacks.clear()
            raise
        else:
            callbacks, self._callbacks = self._callbacks, []
            for callback in callbacks:
                callback()

    def after_commit(self, callback):
        self._callbacks.append(callback)

    def fetchone(self, sql, params=None):
        if "INSERT INTO agent_messages" in sql:
            self.inserts.append(_columns_to_params(sql, tuple(params)))
            return {"id": 1}
        if "SELECT user_id FROM agent_sessions" in sql:
            return {"user_id": None}
        return None

    def fetchall(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        if "INSERT INTO agent_messages" in sql:
            self.inserts.append(_columns_to_params(sql, tuple(params)))

    def _maybe_commit(self, flag):
        return None


def _patched_db(capture: _CaptureDb):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=capture)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes.crm.db.db_session", return_value=cm)


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def test_append_message_persists_display_fields():
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    db = _CaptureDb()
    emit = MagicMock()
    with _patched_db(db), patch(
        "hermes_cloud.app.interactions.emitter.emit", emit
    ):
        PgSessionDB().append_message(
            "s1", "user", content="[context summary]",
            api_content="raw sent bytes",
            display_kind="hidden",
            display_metadata={"summary_of": 42},
        )
    assert len(db.inserts) == 1
    row = db.inserts[0]
    assert row["api_content"] == "raw sent bytes"
    assert row["display_kind"] == "hidden"
    assert json.loads(row["display_metadata"]) == {"summary_of": 42}
    # hidden rows are presentation-suppressed — never fanned out to the
    # interactions projection (SPA/search have no client-side filter).
    emit.assert_not_called()


def test_append_message_defaults_stay_null_and_emit_fires():
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    db = _CaptureDb()
    emit = MagicMock()
    with _patched_db(db), patch(
        "hermes_cloud.app.interactions.emitter.emit", emit
    ):
        PgSessionDB().append_message("s1", "user", content="hello")
    row = db.inserts[0]
    assert row["api_content"] is None
    assert row["display_kind"] is None
    assert row["display_metadata"] is None
    emit.assert_called_once()
    assert "display_kind" not in emit.call_args.kwargs["content"]


def test_append_message_non_hidden_display_kind_rides_into_interactions():
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    db = _CaptureDb()
    emit = MagicMock()
    with _patched_db(db), patch(
        "hermes_cloud.app.interactions.emitter.emit", emit
    ):
        PgSessionDB().append_message(
            "s1", "user", content="switched to gpt-x",
            display_kind="model_switch",
        )
    emit.assert_called_once()
    assert emit.call_args.kwargs["content"]["display_kind"] == "model_switch"


def test_append_message_display_fields_do_not_trip_kwargs_drift_warning():
    """The exact regression this change closes: non-None display fields used
    to fall into ``**kwargs`` and get WARNED-and-dropped."""
    from hermes_cloud.app import agent_session_pg

    db = _CaptureDb()
    with _patched_db(db), patch(
        "hermes_cloud.app.interactions.emitter.emit", MagicMock()
    ), patch.object(agent_session_pg.logger, "warning") as warn:
        agent_session_pg.PgSessionDB().append_message(
            "s1", "user", content="x",
            api_content="y", display_kind="hidden", display_metadata={"a": 1},
        )
    drift_warnings = [
        c for c in warn.call_args_list
        if "unpersisted param" in str(c.args[0])
    ]
    assert not drift_warnings


def test_append_messages_batch_passes_display_fields_through():
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    seen: list[dict] = []
    inst = PgSessionDB()
    with _patched_db(_CaptureDb()), patch.object(
        PgSessionDB, "append_message",
        side_effect=lambda **kw: seen.append(kw) or 1,
    ):
        n = inst.append_messages_batch(
            session_id="s1",
            messages=[
                {
                    "role": "user", "content": "[context summary]",
                    "display_kind": "hidden",
                    "display_metadata": {"k": 1},
                    "api_content": "bytes",
                    "_db_persisted": True,  # underscore keys must be stripped
                },
                {"role": "assistant", "content": "hi",
                 "display_kind": None, "api_content": None},
            ],
            compression_lock_holder="turn-1",
        )
    assert n == 2
    assert seen[0]["display_kind"] == "hidden"
    assert seen[0]["display_metadata"] == {"k": 1}
    assert seen[0]["api_content"] == "bytes"
    assert "_db_persisted" not in seen[0]
    # None values flow through harmlessly (explicit params now, not kwargs).
    assert seen[1]["display_kind"] is None


def test_replace_messages_persists_display_fields():
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    db = _CaptureDb()
    with _patched_db(db):
        PgSessionDB().replace_messages("s1", [
            {"role": "user", "content": "[context summary]",
             "display_kind": "hidden", "display_metadata": {"m": 2},
             "api_content": "sent"},
        ])
    row = db.inserts[0]
    assert row["display_kind"] == "hidden"
    assert json.loads(row["display_metadata"]) == {"m": 2}
    assert row["api_content"] == "sent"


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


class _ReadDb:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self, sql, params=None):
        return list(self._rows)

    def fetchone(self, sql, params=None):
        return None


def _patched_read_db(rows: list[dict]):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=_ReadDb(rows))
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes.crm.db.db_session", return_value=cm)


def _conversation_row(**over) -> dict:
    base = {
        "role": "user", "content": "hello", "tool_call_id": None,
        "tool_calls": None, "tool_name": None, "reasoning": None,
        "reasoning_content": None, "reasoning_details": None,
        "codex_reasoning_items": None, "codex_message_items": None,
        "finish_reason": None, "api_content": None, "display_kind": None,
        "display_metadata": None,
        "message_uid": None, "absorbed_message_uids": None,
        "tool_call_uids": None, "tool_call_uid": None,
    }
    base.update(over)
    return base


def test_conversation_projection_surfaces_display_fields():
    """Mirror sqlite ``_rows_to_conversation``: truthy fields surface as
    message keys; the gateway replay path depends on them (#80622 hidden
    filter, api_content cache-stable substitution)."""
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    rows = [
        _conversation_row(
            content="[context summary]", display_kind="hidden",
            display_metadata={"summary": True}, api_content="sent-bytes",
        ),
        _conversation_row(content="real question"),
    ]
    with _patched_read_db(rows):
        conv = PgSessionDB().get_messages_as_conversation("s1")
    assert conv[0]["display_kind"] == "hidden"
    assert conv[0]["display_metadata"] == {"summary": True}
    assert conv[0]["api_content"] == "sent-bytes"
    # Absent on rows that never carried them (no ``None`` placeholder keys).
    assert "display_kind" not in conv[1]
    assert "display_metadata" not in conv[1]
    assert "api_content" not in conv[1]


def test_get_messages_decodes_double_encoded_display_metadata():
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    rows = [
        {"id": 1, "session_id": "s1", "role": "user", "content": "x",
         "display_metadata": json.dumps({"task_count": 3})},
        {"id": 2, "session_id": "s1", "role": "user", "content": "y",
         "display_metadata": {"already": "parsed"}},
    ]
    with _patched_read_db(rows):
        msgs = PgSessionDB().get_messages("s1")
    assert msgs[0]["display_metadata"] == {"task_count": 3}
    assert msgs[1]["display_metadata"] == {"already": "parsed"}


# ---------------------------------------------------------------------------
# Real-PG round-trip
# ---------------------------------------------------------------------------


@pytest.mark.requires_real_pg
def test_display_fields_round_trip_real_pg():
    """Full write→read round-trip against live PG, batch flush included —
    the exact path run_agent uses at turn end."""
    import uuid

    from hermes_cloud.app.agent_session_pg import PgSessionDB
    from hermes.crm.db import db_session

    sdb = PgSessionDB()
    sid = f"test-display-fields-{uuid.uuid4().hex[:12]}"
    sdb.create_session(sid, source="cli")
    try:
        sdb.append_messages_batch(
            session_id=sid,
            messages=[
                {"role": "user", "content": "[context summary] …",
                 "display_kind": "hidden",
                 "display_metadata": {"compressed_from": "old-session"},
                 "api_content": "exact sent bytes"},
                {"role": "user", "content": "real user turn"},
            ],
        )
        msgs = sdb.get_messages(sid)
        assert len(msgs) == 2
        assert msgs[0]["display_kind"] == "hidden"
        assert msgs[0]["display_metadata"] == {"compressed_from": "old-session"}
        assert msgs[0]["api_content"] == "exact sent bytes"
        assert msgs[1].get("display_kind") is None

        conv = sdb.get_messages_as_conversation(sid)
        assert conv[0]["display_kind"] == "hidden"
        assert conv[0]["display_metadata"] == {"compressed_from": "old-session"}
        assert conv[0]["api_content"] == "exact sent bytes"
        assert "display_kind" not in conv[1]

        # The SPA listing filter shape: hidden rows stay out.
        with db_session() as db:
            visible = db.fetchall(
                "SELECT role, content FROM agent_messages "
                "WHERE session_id = %s "
                "AND (display_kind IS DISTINCT FROM 'hidden') "
                "ORDER BY timestamp, id",
                (sid,),
            ) or []
        assert [r["content"] for r in visible] == ["real user turn"]
    finally:
        sdb.delete_session(sid)
