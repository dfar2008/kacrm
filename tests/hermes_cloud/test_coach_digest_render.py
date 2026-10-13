"""Coach digest rendering — read_coach_proposals must surface context_line,
and the renderer must mechanically inline every card's fields."""
from __future__ import annotations

from hermes.crm.insights.coach_cards import read_coach_proposals


class _FakeDb:
    """Minimal stand-in: read_coach_proposals only calls .fetchall."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self, sql, args):
        return self._rows


def _row(**over):
    base = {
        "id": "1",
        "module": "leads",
        "record_id": 90,
        "severity": "medium",
        "hint": "丁丰华 昨晚飞书留资",
        "reasoning": "24h 内新留资",
        "context_line": "手机 1380016633 · 来源 飞书",
        "draft": "丁丰华您好……",
        "intended_channel": "feishu",
        "render_targets": None,
        "state": "pending_approval",
        "created_at": "2026-05-21T06:15:00Z",
    }
    base.update(over)
    return base


def test_read_coach_proposals_surfaces_context_line():
    rows = read_coach_proposals(_FakeDb([_row()]), user_id=1)
    assert rows[0]["context_line"] == "手机 1380016633 · 来源 飞书"


def test_read_coach_proposals_context_line_missing_is_none():
    row = _row()
    del row["context_line"]
    rows = read_coach_proposals(_FakeDb([row]), user_id=1)
    assert rows[0]["context_line"] is None


from hermes.crm.insights import coach_cards


def _card(**over):
    base = {
        "severity": "medium",
        "hint": "丁丰华 昨晚飞书留资",
        "context_line": "手机 1380016633 · 来源 飞书",
        "draft": "丁丰华您好，收到您的飞书登记……",
    }
    base.update(over)
    return base


def test_render_digest_text_empty_is_none():
    assert coach_cards._render_digest_text([]) is None


def test_render_digest_text_single_card_inlines_everything():
    text = coach_cards._render_digest_text([_card()])
    assert "🤖 今日 Coach" in text
    assert "共 1 件" in text
    assert "🟡" in text
    assert "丁丰华 昨晚飞书留资" in text
    assert "手机 1380016633 · 来源 飞书" in text
    assert "话术：丁丰华您好，收到您的飞书登记……" in text
    # No "go to inbox / web" leakage.
    assert "inbox" not in text
    assert "web" not in text


def test_render_digest_text_high_sorts_first_and_counts():
    text = coach_cards._render_digest_text([
        _card(severity="medium", hint="普通项"),
        _card(severity="high", hint="紧急项"),
    ])
    assert "共 2 件（1 紧急 · 1 普通）" in text
    assert text.index("紧急项") < text.index("普通项")
    assert "🔴" in text


def test_render_digest_text_all_normal_breakdown():
    text = coach_cards._render_digest_text([_card(), _card(hint="第二条")])
    assert "共 2 件（全部普通）" in text


def test_render_digest_text_all_high_breakdown():
    text = coach_cards._render_digest_text([_card(severity="high")])
    assert "共 1 件（全部紧急）" in text


def test_render_digest_text_missing_context_line_ok():
    text = coach_cards._render_digest_text([_card(context_line=None)])
    assert "丁丰华 昨晚飞书留资" in text
    assert "话术：" in text


def test_render_digest_text_missing_draft_skips_script_line():
    text = coach_cards._render_digest_text([_card(draft=None)])
    assert "丁丰华 昨晚飞书留资" in text
    assert "话术：" not in text


def test_render_coach_digest_reads_then_renders(monkeypatch):
    monkeypatch.setattr(
        coach_cards, "read_coach_proposals",
        lambda db, user_id, **kw: [_card()],
    )
    text = coach_cards.render_coach_digest(object(), 1)
    assert "话术：丁丰华您好" in text


def test_render_coach_digest_zero_cards_is_none(monkeypatch):
    monkeypatch.setattr(
        coach_cards, "read_coach_proposals",
        lambda db, user_id, **kw: [],
    )
    assert coach_cards.render_coach_digest(object(), 1) is None
