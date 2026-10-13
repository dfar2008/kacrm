"""Profile-id propagation through the manual touchpoint adapters.

Regression test for the P8-S3 e2e finding: chat / note touchpoints used
to drop ``profile_id`` on the floor, so the async-intake task crashed
with ``rl_agent_drafts_profile_id_fkey`` (empty string fails FK to
``profiles.id``). Now the api layer resolves the user's default profile
and the adapters propagate it onto the ``user`` actor; the emitter's
``_extract_profile_id`` then picks it up for the celery handoff.
"""
from __future__ import annotations


def test_chat_adapter_propagates_profile_id_onto_user_actor():
    from hermes_cloud.app.touchpoints.manual import ChatBoxAdapter

    norm = ChatBoxAdapter().normalize(
        {"user_id": 1, "profile_id": "1", "text": "hi"}
    )

    assert norm.actors == [{"type": "user", "id": 1, "profile_id": "1"}]


def test_chat_adapter_omits_profile_id_when_blank():
    from hermes_cloud.app.touchpoints.manual import ChatBoxAdapter

    norm = ChatBoxAdapter().normalize(
        {"user_id": 1, "profile_id": "", "text": "hi"}
    )

    # No profile_id key when unresolved — keeps actor JSON tight.
    assert norm.actors == [{"type": "user", "id": 1}]


def test_note_adapter_propagates_profile_id_onto_user_actor():
    from hermes_cloud.app.touchpoints.manual import ManualNoteAdapter

    norm = ManualNoteAdapter().normalize(
        {"user_id": 7, "profile_id": "42", "module": "accounts",
         "record_id": 17, "summary": "called"}
    )

    assert {"type": "user", "id": 7, "profile_id": "42"} in norm.actors


def test_emitter_extract_profile_id_finds_value_on_actor():
    from hermes_cloud.app.interactions.emitter import _extract_profile_id

    actors = [{"type": "user", "id": 1, "profile_id": "1"}]
    assert _extract_profile_id(actors) == "1"


def test_emitter_extract_profile_id_returns_empty_when_missing():
    from hermes_cloud.app.interactions.emitter import _extract_profile_id

    assert _extract_profile_id([{"type": "user", "id": 1}]) == ""
    assert _extract_profile_id(None) == ""
    assert _extract_profile_id([]) == ""


def test_resolve_profile_id_keeps_real_value():
    from hermes_cloud.app.touchpoints.api import _resolve_profile_id

    # A non-placeholder string is treated as a real id and returned
    # verbatim — no DB lookup required.
    assert _resolve_profile_id(1, {"profile_id": "1506"}) == "1506"


def test_resolve_profile_id_treats_default_slug_as_placeholder(monkeypatch):
    """``profile_id='default'`` should trigger DB lookup, not be kept as-is."""
    from hermes_cloud.app.touchpoints import api as tp_api

    captured = {}

    class _FakeDb:
        def fetchone(self, sql, args):
            captured["sql"] = sql
            captured["args"] = args
            return {"id": "9"}

    class _FakeSession:
        def __enter__(self):
            return _FakeDb()
        def __exit__(self, *a):
            return False

    def _fake_db_session():
        return _FakeSession()

    # Patch the lazy import target inside _resolve_profile_id.
    import hermes.crm.db as crm_db
    monkeypatch.setattr(crm_db, "db_session", _fake_db_session)

    assert tp_api._resolve_profile_id(1, {"profile_id": "default"}) == "9"
    # Confirms we actually looked up by user_id rather than returning "default".
    assert captured["args"] == (1,)
