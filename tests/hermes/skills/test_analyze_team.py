"""Test analyze_team mini-agent prefetch + LLM call.

Patches use the module reference (``at_mod``) directly instead of the
dotted path string ``"hermes.skills.crm_analyst.analyze_team.X"``. The
package's ``__init__.py`` does ``from hermes.skills.crm_analyst.
analyze_team import analyze_team`` which shadows the submodule attr at
the package level — so ``hermes.skills.crm_analyst.analyze_team``
resolved as a dotted path returns the FUNCTION, not the module.
Direct module reference bypasses that.
"""
import importlib
import json
from unittest.mock import MagicMock, patch

from hermes.skills._utils.llm import SkillLLMError

# Use importlib to load the submodule explicitly. Package __init__'s
# eager ``from .analyze_team import analyze_team`` shadows the attribute
# path; importlib.import_module bypasses the package-attr lookup.
at_mod = importlib.import_module("hermes.skills.crm_analyst.analyze_team")
analyze_team = at_mod.analyze_team
_prefetch_indicators = at_mod._prefetch_indicators


def test_prefetch_collects_record_level_indicators():
    """v2: prefetch must surface record-level detail (sleeping_deals
    list with IDs, pipeline stuck deals, member load) — not just
    aggregate counts. The decision-grade prompt depends on it."""
    db = MagicMock()
    with patch.object(at_mod, "ai_actions_summary",
                      return_value={"total": 10}) as m_actions, \
         patch.object(at_mod, "recommendation_accuracy",
                      return_value=[]), \
         patch.object(at_mod, "sleeping_deals_detail",
                      return_value={"weeks": [], "current": []}) as m_sleep, \
         patch.object(at_mod, "pipeline_funnel_detail",
                      return_value={"stages": [], "stuck": []}) as m_funnel, \
         patch.object(at_mod, "member_load_and_acceptance",
                      return_value=[]) as m_load, \
         patch.object(at_mod, "revenue_summary",
                      return_value={"current_period": {"won_count": 0, "won_amount": 0.0, "deals": []}, "previous_period": {"won_count": 0, "won_amount": 0.0}, "by_owner": []}) as m_rev, \
         patch.object(at_mod, "order_pipeline",
                      return_value={"by_status": [], "recent": []}) as m_ord, \
         patch.object(at_mod, "product_sales_top",
                      return_value=[]) as m_prod, \
         patch.object(at_mod, "recent_interactions",
                      return_value=[]):
        ctx = _prefetch_indicators(
            db, caller_id=1, scope="team", owner_id=None,
        )
        # Keys present:
        for k in ("ai_actions_summary", "recommendation_accuracy",
                  "sleeping_deals", "pipeline_funnel",
                  "member_load_and_acceptance",
                  "revenue_summary", "order_pipeline", "product_sales_top",
                  "recent_interactions"):
            assert k in ctx, f"missing indicator key: {k}"
        m_actions.assert_called_once()
        m_sleep.assert_called_once()
        m_funnel.assert_called_once()
        m_load.assert_called_once()
        m_rev.assert_called_once()
        m_ord.assert_called_once()
        m_prod.assert_called_once()


_SAMPLE_REPLY = {
    "reply_messages": [
        {
            "type": "card",
            "headline": "张三接受率 0%",
            "evidence": {"kind": "sparkline", "data": [0.7, 0.5, 0.3, 0.0]},
            "reasoning": "...",
            "actions": [
                {"label": "看 4 条", "type": "navigate_interactions", "params": {}},
            ],
        },
    ],
    "thread_metadata": {"context_used": ["ai_actions_summary"]},
}


def test_analyze_team_returns_parsed_json(monkeypatch):
    monkeypatch.setattr(at_mod, "_prefetch_indicators",
                        lambda *a, **k: {"ai_actions_summary": {"total": 0},
                                         "recent_interactions": []})
    monkeypatch.setattr(at_mod, "load_skill_body",
                        lambda name: "you are a CRM analyst")
    with patch.object(at_mod, "run_llm_utility",
                      return_value=json.dumps(_SAMPLE_REPLY)):
        out = analyze_team(
            db=object(), caller_id=1, query="本周谁需要关注", scope="team",
        )
    assert out["reply_messages"][0]["headline"] == "张三接受率 0%"
    assert "thread_metadata" in out


def test_analyze_team_llm_failure_returns_error_payload(monkeypatch):
    monkeypatch.setattr(at_mod, "_prefetch_indicators", lambda *a, **k: {})
    monkeypatch.setattr(at_mod, "load_skill_body", lambda name: "p")
    with patch.object(at_mod, "run_llm_utility",
                      side_effect=SkillLLMError("transport")):
        out = analyze_team(
            db=object(), caller_id=1, query="本周怎么样", scope="team",
        )
    assert out["error"] is not None
    assert out["reply_messages"] == []


def test_analyze_team_invalid_json_returns_error(monkeypatch):
    monkeypatch.setattr(at_mod, "_prefetch_indicators", lambda *a, **k: {})
    monkeypatch.setattr(at_mod, "load_skill_body", lambda name: "p")
    with patch.object(at_mod, "run_llm_utility",
                      return_value="not a JSON object {{{"):
        out = analyze_team(
            db=object(), caller_id=1, query="x", scope="team",
        )
    assert out["error"] == "parse_failed"
