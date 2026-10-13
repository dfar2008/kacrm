"""Memory ingest entity extraction — deterministic helpers (no DB).

The full ``ingest_interaction`` path touches pgvector + interactions,
so we leave that to integration tests. Here we pin the behaviour of
``default_extract_entities`` and ``_render_content``, which are pure
and directly affect memory recall quality.
"""
from __future__ import annotations

from hermes.memory.ingest import (
    _render_content, default_extract_entities,
)


def test_render_content_extracts_structured_fields():
    row = {
        "kind": "email",
        "content": {
            "subject": "Re: proposal",
            "text": "Following up on our meeting.",
        },
    }
    out = _render_content(row)
    assert "[email]" in out
    assert "Re: proposal" in out
    assert "Following up" in out


def test_render_content_handles_crm_write_op():
    row = {
        "kind": "agent_draft",
        "content": {
            "op": "update",
            "fields": ["status", "priority"],
        },
    }
    out = _render_content(row)
    assert "op=update" in out
    assert "status" in out


def test_render_content_string_content():
    row = {"kind": "note", "content": "hello world"}
    assert _render_content(row) == "hello world"


def test_extract_entities_from_text_company():
    pairs = default_extract_entities(
        "We met with Acme Corp and Initech Inc yesterday.",
        subjects=None,
    )
    types = {t for t, _ in pairs}
    assert "org" in types
    orgs = [c for t, c in pairs if t == "org"]
    assert any("Acme" in c for c in orgs)


def test_extract_entities_capped():
    # Very long text with many matches should still be bounded.
    text = " ".join(f"Company{i} Corp" for i in range(50))
    pairs = default_extract_entities(text, subjects=None)
    assert len(pairs) <= 20
