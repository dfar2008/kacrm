"""``POST /internal/agent/event`` — Charter §一定律 1 + 原语 ① ingest path.

Hermes per-profile subprocesses POST every agent conversation message
here so it lands in the canonical ``interactions`` table instead of a
per-machine ``state.db`` sqlite file. These tests pin:

* Auth: requires the internal key (no key → 401).
* Required fields: missing profile_id/session_id/message_id/agent_name/
  role/text → 400 with ``missing_fields``.
* Validator wiring: invalid agent.message shape (e.g. role=bystander)
  → 400 with ``agent_message_invalid``.
* Idempotency: re-POST of the same (profile_id, session_id, message_id)
  returns ``deduplicated: True`` and does NOT call emit() again.
* Happy path: emit() invoked with the right kind/actors/content/source.

DB-level integration (real INSERT round-trip + trigger fire) was
verified manually against hermes_crm_dev when commit 03 landed; this
file pins the endpoint's request-handling layer in isolation since the
project's conftest stubs out psycopg.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app


@pytest.fixture
def client():
    app = create_app(AppConfig(internal_key="test-internal-key"))
    return TestClient(app)


def _ok_payload(message_id: str = "msg-test-1", **override) -> dict:
    base = {
        "profile_id": "42",
        "session_id": "sess-test",
        "message_id": message_id,
        "agent_name": "intake",
        "role": "assistant",
        "text": "hello world",
        "user_id": 7,
        "tokens": {"input": 10, "output": 5},
        "model": "gpt-4",
    }
    base.update(override)
    return base


def _patch_dedup_miss():
    """Patch db_session so the dedup pre-query returns no existing row."""
    fake_db = MagicMock()
    fake_db.fetchone.return_value = None
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes_cloud.app.main.db_session" if False
                 else "hermes.crm.db.db_session",
                 return_value=cm)


def _patch_dedup_hit(existing_id: str):
    """Patch db_session so the dedup pre-query returns an existing row."""
    fake_db = MagicMock()
    fake_db.fetchone.return_value = {"id": existing_id}
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("hermes.crm.db.db_session", return_value=cm)


# --- Auth -----------------------------------------------------------------


def test_requires_internal_key(client):
    resp = client.post("/internal/agent/event", json=_ok_payload())
    assert resp.status_code == 401


def test_wrong_internal_key_returns_401(client):
    resp = client.post(
        "/internal/agent/event",
        headers={"X-Internal-Key": "wrong"},
        json=_ok_payload(),
    )
    assert resp.status_code == 401


# --- Required fields ------------------------------------------------------


@pytest.mark.parametrize("missing_field", [
    "profile_id", "session_id", "message_id", "agent_name", "role", "text",
])
def test_missing_required_field_returns_400(client, missing_field):
    payload = _ok_payload()
    del payload[missing_field]
    resp = client.post(
        "/internal/agent/event",
        headers={"X-Internal-Key": "test-internal-key"},
        json=payload,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_fields"


# --- Validator wiring -----------------------------------------------------


def test_validator_rejects_invalid_role(client):
    with _patch_dedup_miss():
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(role="bystander"),
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "agent_message_invalid"


def test_tool_role_without_tool_call_id_rejected(client):
    with _patch_dedup_miss():
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(role="tool", text="(tool result)"),
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "agent_message_invalid"


# --- Happy path -----------------------------------------------------------


def test_happy_path_calls_emit_with_correct_args(client):
    with _patch_dedup_miss(), \
         patch("hermes_cloud.app.interactions.emitter.emit") as mock_emit:
        mock_emit.return_value = "interaction-uuid-1"
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["interaction_id"] == "interaction-uuid-1"
    assert body["deduplicated"] is False

    # emit() invoked with the right kind, source, and content shape.
    mock_emit.assert_called_once()
    kw = mock_emit.call_args.kwargs
    assert kw["kind"] == "agent.message"
    assert kw["source"] == "agent:42:intake"
    assert kw["content"]["session_id"] == "sess-test"
    assert kw["content"]["message_id"] == "msg-test-1"
    assert kw["content"]["role"] == "assistant"
    assert kw["content"]["text"] == "hello world"
    assert kw["content"]["model"] == "gpt-4"
    assert kw["content"]["tokens"] == {"input": 10, "output": 5}

    # Actors include both agent + user.
    actors = kw["actors"]
    assert any(a["type"] == "agent" and a["name"] == "intake"
               and a["profile_id"] == "42" for a in actors)
    assert any(a["type"] == "user" and a["id"] == 7 for a in actors)


def test_optional_fields_passed_through(client):
    """tool_calls / reasoning / billing / derived_from / subjects flow into
    content + emit kwargs verbatim — they're part of the training signal."""
    with _patch_dedup_miss(), \
         patch("hermes_cloud.app.interactions.emitter.emit") as mock_emit:
        mock_emit.return_value = "interaction-uuid-2"
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(
                tool_calls=[{"id": "c1", "name": "crm_write", "arguments": {}}],
                reasoning="thinking step by step",
                finish_reason="tool_calls",
                derived_from="parent-interaction-uuid",
                subjects=[{"module": "leads", "id": 42}],
                estimated_cost_usd=0.0123,
            ),
        )
    assert resp.status_code == 200
    kw = mock_emit.call_args.kwargs
    assert kw["content"]["tool_calls"][0]["name"] == "crm_write"
    assert kw["content"]["reasoning"] == "thinking step by step"
    assert kw["content"]["finish_reason"] == "tool_calls"
    assert kw["content"]["estimated_cost_usd"] == 0.0123
    assert kw["derived_from"] == "parent-interaction-uuid"
    assert kw["subjects"] == [{"module": "leads", "id": 42}]


def test_user_id_optional(client):
    """System / tool messages aren't tied to a user turn — endpoint must
    accept the absence of user_id without erroring."""
    payload = _ok_payload()
    payload.pop("user_id")
    with _patch_dedup_miss(), \
         patch("hermes_cloud.app.interactions.emitter.emit") as mock_emit:
        mock_emit.return_value = "interaction-uuid-3"
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=payload,
        )
    assert resp.status_code == 200
    actors = mock_emit.call_args.kwargs["actors"]
    # Only the agent actor; no user actor.
    assert len(actors) == 1
    assert actors[0]["type"] == "agent"


# --- Idempotency ----------------------------------------------------------


def test_dedup_returns_existing_id_without_emit(client):
    """Re-POST of the same (profile_id, session_id, message_id) tuple skips
    emit() entirely and returns the existing interaction_id."""
    with _patch_dedup_hit("existing-uuid-99"), \
         patch("hermes_cloud.app.interactions.emitter.emit") as mock_emit:
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(message_id="msg-already-stored"),
        )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["interaction_id"] == "existing-uuid-99"
    assert body["deduplicated"] is True
    mock_emit.assert_not_called()


# --- occurred_at parsing --------------------------------------------------


def test_occurred_at_iso_string(client):
    with _patch_dedup_miss(), \
         patch("hermes_cloud.app.interactions.emitter.emit") as mock_emit:
        mock_emit.return_value = "x"
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(occurred_at="2026-04-25T10:30:00Z"),
        )
    assert resp.status_code == 200
    occurred = mock_emit.call_args.kwargs["occurred_at"]
    assert occurred is not None
    assert occurred.year == 2026 and occurred.month == 4 and occurred.day == 25


def test_occurred_at_epoch_float(client):
    with _patch_dedup_miss(), \
         patch("hermes_cloud.app.interactions.emitter.emit") as mock_emit:
        mock_emit.return_value = "x"
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(occurred_at=1745568000.0),
        )
    assert resp.status_code == 200
    occurred = mock_emit.call_args.kwargs["occurred_at"]
    assert occurred is not None


def test_occurred_at_malformed_falls_back_to_none(client):
    """Bad timestamp shouldn't 400 — emit() falls back to now() so we don't
    drop messages over a clock formatting issue from hermes side."""
    with _patch_dedup_miss(), \
         patch("hermes_cloud.app.interactions.emitter.emit") as mock_emit:
        mock_emit.return_value = "x"
        resp = client.post(
            "/internal/agent/event",
            headers={"X-Internal-Key": "test-internal-key"},
            json=_ok_payload(occurred_at="not-a-date"),
        )
    assert resp.status_code == 200
    # emit() called with occurred_at=None — emitter defaults to now().
    assert mock_emit.call_args.kwargs["occurred_at"] is None
