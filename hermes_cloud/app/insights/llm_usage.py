"""Single source of truth for LLM usage tracking.

Every successful (or failed-with-partial) ``chat_with_tools`` call
writes one ``kind='agent.message'`` interaction whose content carries
the OpenAI-style usage info reshaped to the trigger's expected schema.
Alembic 0030's ``trg_agent_session_metrics_upsert`` then keeps
``agent_session_metrics`` in sync — that's the table the user-facing
``/api/analytics`` endpoint (用量统计) reads.

Why this path instead of a separate stderr log: avoids duplicate
telemetry surfaces. Operators can run SQL against
``agent_session_metrics`` for both the user popup AND ops monitoring.
Cache hits are NOT recorded here (they aren't LLM events) — they're
free; if cache-hit rate becomes interesting we'll add a small
``llm_cache_events`` companion table later.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from hermes_cloud.app.interactions.emitter import emit
from hermes.crm.db import DbWrapper

logger = logging.getLogger(__name__)


# Per-model $/M-token rates (input, output). Used to project a
# ballpark cost into ``content.estimated_cost_usd`` so the analytics
# popup shows a number even before we wire real billing pipes.
#
# Numbers as of 2026-05; update when providers change list prices or
# when we add new models. Reasoning tokens count toward output.
_PRICING_PER_M_TOKENS: Dict[str, Dict[str, float]] = {
    # opencode-zen freebies — cost = 0
    "big-pickle":         {"input": 0.0,  "output": 0.0},
    # DeepSeek direct — V4 family pricing (snapshot 2026-05).
    # Pro = full reasoner-tier model; Flash = lightweight/fast tier.
    "deepseek-v4-pro":    {"input": 0.55, "output": 2.19},
    "deepseek-v4-flash":  {"input": 0.27, "output": 1.10},
    # Legacy alias still appears in older sessions / opencode-zen mirror.
    "deepseek-r1":        {"input": 0.55, "output": 2.19},
    # OpenAI
    "gpt-4o":             {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":        {"input": 0.15, "output": 0.60},
    "o1-mini":            {"input": 1.10, "output": 4.40},
    "o3-mini":            {"input": 1.10, "output": 4.40},
    # Anthropic
    "claude-3-5-sonnet":  {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku":   {"input": 0.80, "output": 4.00},
    # Claude 4.x family — pricing snapshot from
    # https://aws.amazon.com/bedrock/pricing/ via hermes
    # agent/usage_pricing.py (v2026.5.7). Substring match catches
    # "anthropic.claude-opus-4-7" / "claude-opus-4-7-20260301" / etc.
    "claude-opus-4":      {"input": 15.00, "output": 75.00},
    "claude-sonnet-4":    {"input": 3.00,  "output": 15.00},
    "claude-haiku-4":     {"input": 0.80,  "output": 4.00},
    # Default (unknown model) — assume free so we don't surprise users
    # with confabulated bills.
    "_default":           {"input": 0.0,  "output": 0.0},
}


def _estimate_cost_usd(
    model: Optional[str], input_tokens: int, output_tokens: int,
) -> float:
    if not model:
        return 0.0
    name = model.lower()
    pricing = _PRICING_PER_M_TOKENS.get(name)
    if not pricing:
        # Substring fallback for naming variants (e.g. "deepseek-r1-0528",
        # "anthropic.claude-opus-4-7-20260301"). Iterate longest-key-first
        # so the most-specific entry wins — without this, a future model
        # like "claude-3-5-sonnet-4-something" would resolve to the
        # 3.5-sonnet rate instead of the sonnet-4 rate.
        for key in sorted(
            (k for k in _PRICING_PER_M_TOKENS if k != "_default"),
            key=len, reverse=True,
        ):
            if key in name:
                pricing = _PRICING_PER_M_TOKENS[key]
                break
    if not pricing:
        pricing = _PRICING_PER_M_TOKENS["_default"]
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
    )


# Phase 4 plan v2.2 §4.3 / §10.1 — actor.name alignment.
#
# Pre-Phase-4: every row hard-coded ``profile_id="copilot"`` and used
# the short-label ``purpose`` (``record_question`` / ``daily_brief`` /
# ``copilot_send`` …) as ``actor.name``.  Analytics' "by_agent_name"
# view then mixed cloud-side LLM utilities with the conversational
# Copilot under one bucket — and once Phase 4 routes chat through
# hermes, hermes' rows would land under a *different* profile_id,
# splitting one logical agent across two names.
#
# Map purpose → hermes profile_id so cloud rows match hermes rows for
# the same logical agent.  The mapping is intentionally additive: an
# unmapped purpose falls back to the conversational ``crm_assistant``
# (matches hermes' default agent) rather than crashing — keeps
# forward-compat when Stage B/C/D add new purposes.
_PURPOSE_TO_PROFILE_ID: Dict[str, str] = {
    # Cloud-side LLM utilities (plan §10.1 — stays cloud, single-shot).
    "record_question":  "question_generator",
    "daily_brief":      "brief_generator",
    # Conversational SPA Copilot (canonical, served by cloud loop in
    # Stage A; routed through hermes from Stage C onward — same
    # profile_id either way so analytics stays seamless).
    "copilot_send":     "crm_assistant",
    "chatbox":          "crm_assistant",
}

# Default profile when ``purpose`` doesn't match any of the explicit
# entries above. Most commonly this catches:
#   - ``surface:<kind>``         (proxy/dispatch hermes-served calls)
#   - ``surface:<kind>_shadow``  (Phase 4 Day 3 shadow double-write)
# both of which are conversational dispatches → ``crm_assistant``,
# with the shadow path getting a ``_shadow`` suffix so Stage A's
# metrics gate can split canonical vs. shadow rows.
_DEFAULT_PROFILE_ID = "crm_assistant"


def _profile_id_for(purpose: str) -> str:
    """Map ``purpose`` → hermes profile_id (Phase 4 §4.3)."""
    if purpose in _PURPOSE_TO_PROFILE_ID:
        return _PURPOSE_TO_PROFILE_ID[purpose]
    # ``surface:<kind>_shadow`` → ``crm_assistant_shadow`` so analytics
    # can filter Stage A's shadow rows out of canonical SLO/quality
    # numbers without parsing the whole purpose string.
    if purpose.endswith("_shadow"):
        return f"{_DEFAULT_PROFILE_ID}_shadow"
    return _DEFAULT_PROFILE_ID


def record_llm_usage(
    *,
    user_id: int,
    purpose: str,
    msg: Dict[str, Any],
    db: Optional[DbWrapper] = None,
    session_id: Optional[str] = None,
) -> None:
    """Emit one ``agent.message`` interaction so the trigger updates
    ``agent_session_metrics``. Call this AFTER a successful
    ``chat_with_tools(...)`` whose returned ``msg`` has ``__usage``
    and ``__model``.

    ``purpose`` is short label like ``record_question`` /
    ``daily_brief`` / ``copilot_send`` — surfaces in
    ``agent_session_metrics.agent_name`` so analytics can break
    usage down by what the LLM was for.

    ``session_id`` lets multiple calls roll up under one
    ``agent_session_metrics`` row (the trigger upserts on
    ``profile_id, session_id``). Default is a fresh UUID per call.
    """
    usage = msg.get("__usage") if isinstance(msg, dict) else None
    if not isinstance(usage, dict):
        return  # provider didn't return usage info → silent skip

    model = (msg.get("__model") if isinstance(msg, dict) else None) or "unknown"
    input_t = int(usage.get("prompt_tokens") or 0)
    output_t = int(usage.get("completion_tokens") or 0)
    # Provider-specific nested fields (OpenAI-compatible response shape).
    cache_read_t = 0
    pdetails = usage.get("prompt_tokens_details")
    if isinstance(pdetails, dict):
        cache_read_t = int(pdetails.get("cached_tokens") or 0)
    reasoning_t = 0
    cdetails = usage.get("completion_tokens_details")
    if isinstance(cdetails, dict):
        reasoning_t = int(cdetails.get("reasoning_tokens") or 0)

    sid = session_id or f"{purpose}-{uuid.uuid4().hex[:12]}"
    mid = uuid.uuid4().hex

    # Phase 4 §4.3: actor.profile_id / actor.name align with hermes'
    # profile_id naming. ``purpose`` is still recorded under
    # ``content.purpose`` for drill-down (e.g. tell apart two
    # ``crm_assistant`` rows where one came from chatbox and one from
    # SPA Copilot).
    profile_id = _profile_id_for(purpose)

    try:
        emit(
            kind="agent.message",
            actors=[
                {"type": "user", "id": int(user_id)},
                {
                    "type": "agent",
                    "profile_id": profile_id,
                    "name": profile_id,
                },
            ],
            subjects=None,
            content={
                "session_id": sid,
                # message_id + text are required by emitter._validate_agent_message;
                # we don't have a real assistant body here (Copilot's text content is
                # stored elsewhere — this row is purely for usage accounting), so we
                # synthesize a stable id and a marker text.
                "message_id": mid,
                "role": "assistant",
                "text": f"[usage:{purpose}]",
                "model": model,
                "tokens": {
                    "input": input_t,
                    "output": output_t,
                    "cache_read": cache_read_t,
                    "cache_write": 0,
                    "reasoning": reasoning_t,
                },
                "estimated_cost_usd": _estimate_cost_usd(model, input_t, output_t),
                "purpose": purpose,
            },
            source=f"copilot:{purpose}",
            db=db,
        )
    except Exception:
        logger.exception(
            "record_llm_usage_failed user=%s purpose=%s",
            user_id, purpose,
        )
