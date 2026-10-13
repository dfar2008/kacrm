"""Memory ingest scope policy — charter §10 cross-agent sharing.

Before this fix the ingest path wrote every user-authored interaction
as ``scope='user'``, which meant memories about a shared business
record (``potentials#42``, ``accounts#7``) were invisible to the
salesperson's colleagues. The org-scope architecture in the schema
and in ``search_memories`` was dead code.

New policy (_resolve_scope):

  - interaction.kind ∈ HERMES_MEMORY_PRIVATE_KINDS     → user
  - subjects contains a CRM record                     → org
  - otherwise with an actor user                       → user
  - no actor, no CRM record                            → org

These unit tests pin the branch table. The search-side union
(``user_id = %s OR scope = 'org'``) was already there — now it
actually has org rows to find.
"""
from __future__ import annotations

from hermes.memory.ingest import _resolve_scope, _has_crm_subject


def test_crm_subject_promotes_to_org_scope(monkeypatch):
    monkeypatch.delenv("HERMES_MEMORY_PRIVATE_KINDS", raising=False)
    scope, uid = _resolve_scope(
        interaction_kind="crm.write",
        subjects=[{"module": "potentials", "id": 42}],
        user_id=7,
    )
    assert scope == "org"
    assert uid is None


def test_no_subjects_stays_user_scoped(monkeypatch):
    monkeypatch.delenv("HERMES_MEMORY_PRIVATE_KINDS", raising=False)
    scope, uid = _resolve_scope(
        interaction_kind="chat.message",
        subjects=[],
        user_id=7,
    )
    assert scope == "user"
    assert uid == 7


def test_no_actor_no_subject_defaults_to_org(monkeypatch):
    """System-emitted interactions with no user and no CRM subject —
    e.g. scheduled report runs — go to org scope so nobody gets locked
    out of them."""
    monkeypatch.delenv("HERMES_MEMORY_PRIVATE_KINDS", raising=False)
    scope, uid = _resolve_scope(
        interaction_kind="system.tick",
        subjects=None,
        user_id=None,
    )
    assert scope == "org"
    assert uid is None


def test_private_kinds_override_keeps_user_scope(monkeypatch):
    monkeypatch.setenv("HERMES_MEMORY_PRIVATE_KINDS", "personal_note,private_memo")
    # Even with a CRM subject, private_memo stays user-scoped.
    scope, uid = _resolve_scope(
        interaction_kind="private_memo",
        subjects=[{"module": "potentials", "id": 42}],
        user_id=7,
    )
    assert scope == "user"
    assert uid == 7


def test_private_kinds_with_no_actor_falls_through_to_org(monkeypatch):
    monkeypatch.setenv("HERMES_MEMORY_PRIVATE_KINDS", "private_memo")
    scope, uid = _resolve_scope(
        interaction_kind="private_memo",
        subjects=[{"module": "potentials", "id": 1}],
        user_id=None,
    )
    # Private kind but no owning user → can't be user-scoped; org as safe default.
    assert scope == "org"
    assert uid is None


def test_has_crm_subject_requires_both_module_and_id():
    assert _has_crm_subject([{"module": "potentials", "id": 42}]) is True
    assert _has_crm_subject([{"module": "potentials"}]) is False
    assert _has_crm_subject([{"id": 42}]) is False
    assert _has_crm_subject([]) is False
    assert _has_crm_subject(None) is False
    # Mixed — still true if any one is complete
    assert _has_crm_subject([
        {"module": "potentials"},
        {"module": "accounts", "id": 7},
    ]) is True
