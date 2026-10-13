"""coach_proposal_submit must accept and persist an optional context_line —
a one-line salesperson-facing metadata string (phone / source / days
since contact) that the card-driven digest renders verbatim."""
from __future__ import annotations


def test_schema_includes_context_line():
    from hermes.tools.crm.dispatch import _coach_proposal_submit_tool

    props = _coach_proposal_submit_tool().input_schema["properties"]
    assert "context_line" in props
    assert props["context_line"]["type"] == "string"


def test_context_line_persisted_into_params(monkeypatch):
    import hermes.action_gateway.dispatcher as gw

    captured = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return {"id": "9", "state": "pending_approval"}

    monkeypatch.setattr(gw, "submit", fake_submit)

    from hermes.tools.crm.dispatch import _dispatch_coach_proposal_submit

    res = _dispatch_coach_proposal_submit(
        {
            "summary": "丁丰华 昨晚飞书留资",
            "action": {"type": "draft_message", "draft": "丁丰华您好……"},
            "severity": "medium",
            "context_line": "手机 1380016633 · 来源 飞书 · 0 天未跟进",
        },
        user_id=1,
        source="cron",
        profile_id="p1",
        agent_name="agent:crm-daily-coach",
        db=None,
    )
    assert res["success"] is True
    assert captured["params"]["context_line"] == "手机 1380016633 · 来源 飞书 · 0 天未跟进"


def test_context_line_omitted_when_absent(monkeypatch):
    import hermes.action_gateway.dispatcher as gw

    captured = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return {"id": "9", "state": "pending_approval"}

    monkeypatch.setattr(gw, "submit", fake_submit)

    from hermes.tools.crm.dispatch import _dispatch_coach_proposal_submit

    _dispatch_coach_proposal_submit(
        {
            "summary": "x",
            "action": {"type": "draft_message", "draft": "y"},
            "severity": "low",
        },
        user_id=1,
        source="cron",
        profile_id="p1",
        agent_name="agent:crm-daily-coach",
        db=None,
    )
    assert "context_line" not in captured["params"]
