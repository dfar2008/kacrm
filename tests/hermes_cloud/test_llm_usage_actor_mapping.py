"""Phase 4 plan v2.2 §4.3 / §10.1 — actor.name / profile_id alignment.

`record_llm_usage` used to hard-code ``profile_id="copilot"`` and use
the short ``purpose`` label as ``actor.name``. After Phase 4 it must
emit hermes-aligned profile ids so the same logical agent shows up
under one name in analytics whether the row was written from the
cloud-side LLM utilities (question_generator / brief_generator) or
from the hermes-served Copilot conversation path.

Stage A's shadow path additionally MUST land under a ``_shadow``
suffix so Stage B's metrics gate can split canonical vs shadow.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from hermes_cloud.app.insights.llm_usage import (
    _profile_id_for,
    record_llm_usage,
)


# ──────────────────────────────────────────────────────────────────
# Pure mapping
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("purpose,expected", [
    # Cloud-side LLM utilities (plan §10.1)
    ("record_question",                  "question_generator"),
    ("daily_brief",                      "brief_generator"),
    # Conversational SPA Copilot (Stage A: cloud loop / Stage C: hermes)
    ("copilot_send",                     "crm_assistant"),
    ("chatbox",                          "crm_assistant"),
    # Surface-tagged hermes dispatch
    ("surface:spa_record_copilot",       "crm_assistant"),
    ("surface:spa_chatbox",              "crm_assistant"),
    ("surface:feishu",                   "crm_assistant"),
    # Phase 4 Day 3 shadow rows MUST suffix _shadow so Stage B's
    # metrics gate can split canonical vs shadow.
    ("surface:spa_record_copilot_shadow","crm_assistant_shadow"),
    ("surface:spa_chatbox_shadow",       "crm_assistant_shadow"),
    # Forward-compat: unknown purpose falls back to crm_assistant
    # rather than crashing — Stage B/C/D will introduce new purposes.
    ("totally_made_up_purpose",          "crm_assistant"),
])
def test_profile_id_mapping(purpose, expected):
    assert _profile_id_for(purpose) == expected


# ──────────────────────────────────────────────────────────────────
# Emit shape — actors/source/content
# ──────────────────────────────────────────────────────────────────


def _capture_emit():
    captured = {}

    def _fake_emit(**kw):
        captured.update(kw)

    return captured, _fake_emit


def _msg(model="big-pickle"):
    return {
        "__usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "__model": model,
    }


def test_emit_uses_mapped_profile_id_for_question_generator():
    captured, fake = _capture_emit()
    with patch("hermes_cloud.app.insights.llm_usage.emit", new=fake):
        record_llm_usage(
            user_id=42, purpose="record_question", msg=_msg(),
        )

    actors = captured["actors"]
    agent = next(a for a in actors if a["type"] == "agent")
    # Phase 4: profile_id is the hermes-aligned name, NOT "copilot"
    assert agent["profile_id"] == "question_generator"
    # actor.name mirrors profile_id (no longer the short purpose label)
    assert agent["name"] == "question_generator"


def test_emit_uses_mapped_profile_id_for_brief_generator():
    captured, fake = _capture_emit()
    with patch("hermes_cloud.app.insights.llm_usage.emit", new=fake):
        record_llm_usage(
            user_id=7, purpose="daily_brief", msg=_msg(),
        )

    agent = next(a for a in captured["actors"] if a["type"] == "agent")
    assert agent["profile_id"] == "brief_generator"
    assert agent["name"] == "brief_generator"


def test_emit_uses_crm_assistant_for_copilot_send():
    captured, fake = _capture_emit()
    with patch("hermes_cloud.app.insights.llm_usage.emit", new=fake):
        record_llm_usage(
            user_id=7, purpose="copilot_send", msg=_msg(),
        )

    agent = next(a for a in captured["actors"] if a["type"] == "agent")
    assert agent["profile_id"] == "crm_assistant"
    assert agent["name"] == "crm_assistant"


def test_emit_distinguishes_shadow_from_canonical():
    """Stage A hard rule (test_copilot_shadow_dispatch.py:282): canonical
    rows MUST NOT collide with shadow rows under the same agent name."""
    canon_captured, canon_emit = _capture_emit()
    shadow_captured, shadow_emit = _capture_emit()

    with patch("hermes_cloud.app.insights.llm_usage.emit", new=canon_emit):
        record_llm_usage(
            user_id=1, purpose="surface:spa_record_copilot", msg=_msg(),
        )
    with patch("hermes_cloud.app.insights.llm_usage.emit", new=shadow_emit):
        record_llm_usage(
            user_id=1,
            purpose="surface:spa_record_copilot_shadow",
            msg=_msg(),
        )

    canon_agent = next(
        a for a in canon_captured["actors"] if a["type"] == "agent"
    )
    shadow_agent = next(
        a for a in shadow_captured["actors"] if a["type"] == "agent"
    )
    # Different bucket — Stage B's metrics gate can split them.
    assert canon_agent["profile_id"] == "crm_assistant"
    assert shadow_agent["profile_id"] == "crm_assistant_shadow"
    assert canon_agent["profile_id"] != shadow_agent["profile_id"]


def test_purpose_still_recorded_in_content_for_drilldown():
    """Two ``crm_assistant`` rows (one from chatbox, one from SPA Copilot)
    must remain distinguishable via ``content.purpose`` so analytics can
    still answer "what fraction of crm_assistant traffic was chatbox?"."""
    rows = []

    def _fake_emit(**kw):
        rows.append(kw)

    with patch("hermes_cloud.app.insights.llm_usage.emit", new=_fake_emit):
        record_llm_usage(user_id=1, purpose="chatbox", msg=_msg())
        record_llm_usage(user_id=1, purpose="copilot_send", msg=_msg())

    assert rows[0]["content"]["purpose"] == "chatbox"
    assert rows[1]["content"]["purpose"] == "copilot_send"
    # Same profile_id — analytics drill-down via content.purpose.
    a0 = next(a for a in rows[0]["actors"] if a["type"] == "agent")
    a1 = next(a for a in rows[1]["actors"] if a["type"] == "agent")
    assert a0["profile_id"] == a1["profile_id"] == "crm_assistant"


def test_source_keeps_copilot_prefix_for_api_filter_compat():
    """``api.py:_is_ai_source`` recognises AI-emitted interactions via
    ``source.startswith("copilot")``. Phase 4 doesn't change source —
    only actor.name / profile_id — so the AI-recent-activity filter on
    /api/insights/feed keeps working without an api.py edit."""
    captured, fake = _capture_emit()
    with patch("hermes_cloud.app.insights.llm_usage.emit", new=fake):
        record_llm_usage(user_id=7, purpose="record_question", msg=_msg())

    assert captured["source"] == "copilot:record_question"
    assert captured["source"].startswith("copilot")


def test_no_usage_skips_emit():
    """Zero-token / missing-usage messages must not write a row —
    keeps analytics clean of empty noise (matches existing behavior)."""
    rows = []
    with patch(
        "hermes_cloud.app.insights.llm_usage.emit",
        new=lambda **kw: rows.append(kw),
    ):
        record_llm_usage(user_id=1, purpose="record_question", msg={})
        record_llm_usage(
            user_id=1, purpose="record_question",
            msg={"__usage": "not-a-dict"},
        )
    assert rows == []
