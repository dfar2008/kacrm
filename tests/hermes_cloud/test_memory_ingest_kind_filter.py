"""Memory ingest must skip agent-execution-trace interactions.

Only genuine customer-touchpoint / conversational kinds (im_message,
email, call, note, …) should become ``memories`` facts. Agent traces
(agent.message, action, copilot_*, auto_drive.*, …) render as raw JSON
via ``_render_content`` and used to pollute the customer-facing "facts"
panel. This pins the kind allowlist that keeps them out.
"""
from __future__ import annotations

from unittest.mock import MagicMock


def test_default_allowlist_has_conversational_kinds():
    from hermes.memory.ingest import _ingestible_kinds
    kinds = _ingestible_kinds()
    for k in ("im_message", "email", "call", "call_transcript",
              "voice_memo", "meeting", "meeting_note", "note"):
        assert k in kinds, f"{k} should be ingestible"


def test_default_allowlist_excludes_agent_and_system_kinds():
    from hermes.memory.ingest import _ingestible_kinds
    kinds = _ingestible_kinds()
    for k in ("agent.message", "action", "copilot_message",
              "copilot_reply", "auto_drive.tick", "auto_drive.team_tick",
              "dashboard_message", "message", "chat", "crm_write",
              "schema_message", "channel.push", "feedback"):
        assert k not in kinds, f"{k} must not be ingested as a fact"


def test_env_override_extends_allowlist(monkeypatch):
    monkeypatch.setenv("HERMES_MEMORY_INGEST_KINDS", "whatsapp_message, sms")
    from hermes.memory.ingest import _ingestible_kinds
    kinds = _ingestible_kinds()
    assert "whatsapp_message" in kinds
    assert "sms" in kinds
    # Defaults are still present — the env var extends, never replaces.
    assert "email" in kinds


def _fake_db(interaction_row):
    """A DbWrapper double: returns ``interaction_row`` for the interactions
    SELECT, ``None`` for every other lookup (idempotency / entity)."""
    db = MagicMock()

    def fetchone(sql, args=None):
        if "FROM interactions" in sql:
            return interaction_row
        return None

    db.fetchone.side_effect = fetchone
    return db


def test_ingest_interaction_skips_agent_message(monkeypatch):
    from hermes.memory import ingest
    upsert = MagicMock()
    monkeypatch.setattr(ingest, "upsert_memory", upsert)
    db = _fake_db({
        "id": "i1", "kind": "agent.message", "occurred_at": None,
        "actors": [], "subjects": [],
        "content": {"role": "tool", "text": '{"success": true}'},
        "source": "agent:1:intake", "provenance": None,
    })
    result = ingest.ingest_interaction("i1", db=db)
    assert result is None
    upsert.assert_not_called()


def test_ingest_interaction_skips_action_trace(monkeypatch):
    from hermes.memory import ingest
    upsert = MagicMock()
    monkeypatch.setattr(ingest, "upsert_memory", upsert)
    db = _fake_db({
        "id": "i3", "kind": "action", "occurred_at": None,
        "actors": [], "subjects": [],
        "content": {"op": "update", "fields": ["stage"]},
        "source": "agent:1:intake", "provenance": None,
    })
    assert ingest.ingest_interaction("i3", db=db) is None
    upsert.assert_not_called()


def test_ingest_interaction_skips_empty_shell_content(monkeypatch):
    """An allowlisted kind whose content renders to just the ``[kind]``
    tag (e.g. an im_message with empty text) produces no memory."""
    from hermes.memory import ingest
    upsert = MagicMock()
    monkeypatch.setattr(ingest, "upsert_memory", upsert)
    db = _fake_db({
        "id": "i4", "kind": "im_message", "occurred_at": None,
        "actors": [], "subjects": [],
        "content": {"text": "", "platform": "feishu"},
        "source": "channel:feishu", "provenance": None,
    })
    assert ingest.ingest_interaction("i4", db=db) is None
    upsert.assert_not_called()


def test_ingest_interaction_allows_note(monkeypatch):
    from hermes.memory import ingest
    from hermes.memory.vector_store import UpsertResult
    upsert = MagicMock(return_value=UpsertResult(
        committed=True, reason=None, memory_id=999))
    monkeypatch.setattr(ingest, "upsert_memory", upsert)
    db = _fake_db({
        "id": "i2", "kind": "note", "occurred_at": None,
        "actors": [], "subjects": [],
        "content": {"text": "客户来电询问报价方案"},
        "source": "manual", "provenance": None,
    })
    result = ingest.ingest_interaction("i2", db=db)
    assert result == 999
    upsert.assert_called_once()
