"""Pure-function tests for :mod:`hermes.memory.ingest`.

Covers five pure helpers:
  ``_render_content``, ``_has_crm_subject``, ``_resolve_scope``,
  ``_actor_user_id``, ``default_extract_entities``

All are free of DB/vector-store access. The module's transitive import chain
hits ``crm.db`` and ``memory.embedder`` / ``memory.entities``, so we stub
those before importing.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _render_content
# ---------------------------------------------------------------------------


def test_render_content_json_string_subject():
    from hermes.memory.ingest import _render_content

    row = {"kind": "email", "content": '{"subject": "Hello World"}'}
    result = _render_content(row)
    assert result == "[email] Hello World"


def test_render_content_json_string_message():
    from hermes.memory.ingest import _render_content

    row = {"kind": "chat", "content": '{"message": "How are you?"}'}
    result = _render_content(row)
    assert result == "[chat] How are you?"


def test_render_content_raw_string():
    from hermes.memory.ingest import _render_content

    row = {"kind": "note", "content": "Just a plain note"}
    assert _render_content(row) == "Just a plain note"


def test_render_content_none_content():
    from hermes.memory.ingest import _render_content

    row = {"kind": "meeting", "content": None}
    assert _render_content(row) == "[meeting]"


def test_render_content_empty_dict_content():
    from hermes.memory.ingest import _render_content

    row = {"kind": "call", "content": {}}
    assert _render_content(row) == "[call]"


def test_render_content_dict_with_op_and_fields():
    from hermes.memory.ingest import _render_content

    row = {
        "kind": "crm_update",
        "content": {"op": "update", "fields": ["name", "phone"]},
    }
    result = _render_content(row)
    assert "op=update" in result
    assert "fields=name,phone" in result


def test_render_content_dict_prefers_title_first():
    from hermes.memory.ingest import _render_content

    row = {"kind": "doc", "content": {"title": "Q1 Report", "body": "Full text"}}
    result = _render_content(row)
    assert "Q1 Report" in result
    assert "Full text" in result


def test_render_content_missing_kind_defaults_to_empty():
    from hermes.memory.ingest import _render_content

    row = {"content": "hello"}
    assert _render_content(row) == "hello"


def test_render_content_non_json_string_content():
    from hermes.memory.ingest import _render_content

    row = {"kind": "note", "content": "not {json at all"}
    assert _render_content(row) == "not {json at all"


# ---------------------------------------------------------------------------
# _has_crm_subject
# ---------------------------------------------------------------------------


def test_has_crm_subject_valid():
    from hermes.memory.ingest import _has_crm_subject

    assert _has_crm_subject([{"module": "accounts", "id": 1}]) is True


def test_has_crm_subject_empty_list():
    from hermes.memory.ingest import _has_crm_subject

    assert _has_crm_subject([]) is False


def test_has_crm_subject_none():
    from hermes.memory.ingest import _has_crm_subject

    assert _has_crm_subject(None) is False


def test_has_crm_subject_missing_module():
    from hermes.memory.ingest import _has_crm_subject

    assert _has_crm_subject([{"id": 1}]) is False


def test_has_crm_subject_id_none():
    from hermes.memory.ingest import _has_crm_subject

    assert _has_crm_subject([{"module": "accounts", "id": None}]) is False


def test_has_crm_subject_id_zero_is_valid():
    from hermes.memory.ingest import _has_crm_subject

    # id=0 is not None — technically a valid (if unusual) record id
    assert _has_crm_subject([{"module": "accounts", "id": 0}]) is True


def test_has_crm_subject_non_dict_entry_skipped():
    from hermes.memory.ingest import _has_crm_subject

    assert _has_crm_subject(["accounts#1", None]) is False


def test_has_crm_subject_multiple_with_one_valid():
    from hermes.memory.ingest import _has_crm_subject

    subjects = [{"id": 5}, {"module": "contacts", "id": 7}]
    assert _has_crm_subject(subjects) is True


# ---------------------------------------------------------------------------
# _actor_user_id
# ---------------------------------------------------------------------------


def test_actor_user_id_returns_int():
    from hermes.memory.ingest import _actor_user_id

    assert _actor_user_id([{"type": "user", "id": "42"}]) == 42


def test_actor_user_id_non_user_type_skipped():
    from hermes.memory.ingest import _actor_user_id

    assert _actor_user_id([{"type": "agent", "id": "7"}]) is None


def test_actor_user_id_empty_list():
    from hermes.memory.ingest import _actor_user_id

    assert _actor_user_id([]) is None


def test_actor_user_id_none():
    from hermes.memory.ingest import _actor_user_id

    assert _actor_user_id(None) is None


def test_actor_user_id_invalid_id_returns_none():
    from hermes.memory.ingest import _actor_user_id

    assert _actor_user_id([{"type": "user", "id": "abc"}]) is None


def test_actor_user_id_non_dict_entry_skipped():
    from hermes.memory.ingest import _actor_user_id

    assert _actor_user_id(["user:5", None]) is None


def test_actor_user_id_first_user_wins():
    from hermes.memory.ingest import _actor_user_id

    actors = [{"type": "bot", "id": "1"}, {"type": "user", "id": "99"}]
    assert _actor_user_id(actors) == 99


# ---------------------------------------------------------------------------
# _resolve_scope
# ---------------------------------------------------------------------------


def test_resolve_scope_crm_subject_is_org():
    from hermes.memory.ingest import _resolve_scope

    scope, uid = _resolve_scope(
        interaction_kind="email",
        subjects=[{"module": "accounts", "id": 1}],
        user_id=7,
    )
    assert scope == "org"
    assert uid is None


def test_resolve_scope_no_subject_with_user_is_user():
    from hermes.memory.ingest import _resolve_scope

    scope, uid = _resolve_scope(
        interaction_kind="note", subjects=[], user_id=5
    )
    assert scope == "user"
    assert uid == 5


def test_resolve_scope_no_subject_no_user_is_org():
    from hermes.memory.ingest import _resolve_scope

    scope, uid = _resolve_scope(
        interaction_kind="system", subjects=None, user_id=None
    )
    assert scope == "org"
    assert uid is None


def test_resolve_scope_private_kind_with_user(monkeypatch):
    from hermes.memory.ingest import _resolve_scope

    monkeypatch.setenv("HERMES_MEMORY_PRIVATE_KINDS", "personal_note")
    scope, uid = _resolve_scope(
        interaction_kind="personal_note",
        subjects=[{"module": "accounts", "id": 1}],
        user_id=3,
    )
    assert scope == "user"
    assert uid == 3


def test_resolve_scope_private_kind_without_user(monkeypatch):
    from hermes.memory.ingest import _resolve_scope

    monkeypatch.setenv("HERMES_MEMORY_PRIVATE_KINDS", "personal_note")
    scope, uid = _resolve_scope(
        interaction_kind="personal_note", subjects=[], user_id=None
    )
    assert scope == "org"
    assert uid is None


# ---------------------------------------------------------------------------
# default_extract_entities
# ---------------------------------------------------------------------------


def test_default_extract_entities_crm_subject():
    from hermes.memory.ingest import default_extract_entities

    result = default_extract_entities("", [{"module": "accounts", "id": 42}])
    assert ("crm:accounts", "accounts#42") not in result


def test_default_extract_entities_deduplicates_subjects():
    from hermes.memory.ingest import default_extract_entities

    subjects = [{"module": "leads", "id": 1}, {"module": "leads", "id": 1}]
    result = default_extract_entities("", subjects)
    assert ("crm:leads", "leads#1") not in result


def test_default_extract_entities_legacy_crm_subject_mode(monkeypatch):
    from hermes.memory.ingest import default_extract_entities

    monkeypatch.setenv("HERMES_MEMORY_LEGACY_CRM_SUBJECT_ENTITIES", "1")
    subjects = [{"module": "leads", "id": 1}, {"module": "leads", "id": 1}]
    result = default_extract_entities("", subjects)
    assert result.count(("crm:leads", "leads#1")) == 1


def test_default_extract_entities_empty():
    from hermes.memory.ingest import default_extract_entities

    assert default_extract_entities("", None) == []


def test_default_extract_entities_caps_at_20():
    from hermes.memory.ingest import default_extract_entities

    subjects = [{"module": "accounts", "id": i} for i in range(25)]
    result = default_extract_entities("", subjects)
    assert len(result) <= 20


def test_default_extract_entities_non_dict_subject_skipped():
    from hermes.memory.ingest import default_extract_entities

    result = default_extract_entities("", ["accounts#1", None])
    assert result == []
