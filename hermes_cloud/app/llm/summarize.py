"""Summarize an interaction's raw body with opencode-zen ``big-pickle``.

Why this module exists
----------------------
``crm.projections.contact_stats`` computes each contact's
``latest_progress`` label from the latest interaction row. When the row
doesn't carry an explicit ``content.summary`` it falls back to
``str(body)[:200]`` — fine for a mock, ugly in real UIs (half-sentences,
no signal). This helper fills that gap: *after* a touchpoint is
ingested, if ``HERMES_AUTO_SUMMARIZE=1`` is set we fire-and-forget a
call to opencode-zen's free ``big-pickle`` model and UPDATE the row's
``content.summary`` in place.

Design
------
* **Free tier**: opencode-zen Zen endpoint (``https://opencode.ai/zen/v1``)
  exposes ``big-pickle`` as a free model — the public "public" API key
  works out of the box. Override via ``HERMES_OPENCODE_ZEN_KEY`` /
  ``HERMES_OPENCODE_ZEN_BASE``.
* **Fire and forget**: ``schedule_interaction_summary`` never awaits the
  HTTP call from the caller's perspective. If the loop is alive we
  ``asyncio.create_task``; otherwise we silently skip (no sync blocking
  in the hot path).
* **Failure is silent**: summarization is a polish, not a correctness
  property. On any exception we log and leave ``content`` untouched —
  ``contact_stats`` will still fall back to ``[:200]``.
* **No PII leaves unless enabled**: default-off flag. The user opts in
  per-deployment.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Dict, Optional

import httpx

from hermes.crm.db import db_session

logger = logging.getLogger(__name__)


AUTO_SUMMARIZE_ENV = "HERMES_AUTO_SUMMARIZE"
_OPENCODE_ZEN_BASE_DEFAULT = "https://opencode.ai/zen/v1"
_OPENCODE_ZEN_MODEL_DEFAULT = "big-pickle"
_OPENCODE_ZEN_KEY_DEFAULT = "public"  # opencode-zen's free-tier key

# Keep prompts short; big-pickle is free but rate-limited.
_MAX_INPUT_CHARS = 4000
_MAX_OUTPUT_TOKENS = 240  # Chinese ~2 tokens/char; give enough room for a 40-char line


def _endpoint() -> str:
    base = os.getenv("HERMES_OPENCODE_ZEN_BASE", _OPENCODE_ZEN_BASE_DEFAULT).rstrip("/")
    return f"{base}/chat/completions"


def _model() -> str:
    return os.getenv("HERMES_OPENCODE_ZEN_MODEL", _OPENCODE_ZEN_MODEL_DEFAULT)


def _api_key() -> str:
    return os.getenv("HERMES_OPENCODE_ZEN_KEY", _OPENCODE_ZEN_KEY_DEFAULT) or _OPENCODE_ZEN_KEY_DEFAULT


def _auto_enabled() -> bool:
    return os.getenv(AUTO_SUMMARIZE_ENV, "").strip() in {"1", "true", "yes", "on"}


async def summarize_text(
    text: str,
    *,
    kind: str = "message",
    timeout_sec: float = 15.0,
) -> Optional[str]:
    """Ask big-pickle for a ≤80-char Chinese one-liner. Returns ``None`` on error."""
    body = (text or "").strip()
    if not body:
        return None
    if len(body) > _MAX_INPUT_CHARS:
        body = body[:_MAX_INPUT_CHARS] + "…"

    sys_prompt = (
        "你是 CRM 助手。把下面的一条 " + kind + " 摘成一句不超过 40 个字的中文进展摘要，"
        "突出客户关心的核心诉求或下一步动作。只输出摘要本身，不要客套、不要引号。"
    )

    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": body},
        ],
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(_endpoint(), json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "summarize_http_%s model=%s body=%s",
                    resp.status_code, _model(), resp.text[:200],
                )
                return None
            data = resp.json()
    except Exception as exc:
        logger.warning("summarize_request_failed: %s", exc)
        return None

    try:
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            # Anthropic-style tool parts: pick first text block
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("text", "output_text"):
                    content = part.get("text") or part.get("content")
                    break
        if not isinstance(content, str):
            return None
        summary = content.strip().strip('"“”「」').strip()
        # Single-line, truncate defensively.
        summary = summary.splitlines()[0] if summary else ""
        if len(summary) > 120:
            summary = summary[:120]
        return summary or None
    except Exception:
        logger.exception("summarize_parse_failed")
        return None


def _update_interaction_summary(interaction_id: str, summary: str) -> None:
    """Merge ``{"summary": ...}`` into ``interactions.content`` for one row."""
    sql = (
        "UPDATE interactions "
        "SET content = COALESCE(content, '{}'::jsonb) || %s::jsonb "
        "WHERE id = %s"
    )
    try:
        with db_session() as db:
            db.execute(sql, (json.dumps({"summary": summary}), interaction_id))
    except Exception:
        logger.exception("update_interaction_summary_failed id=%s", interaction_id)


async def _summarize_and_store(
    interaction_id: str,
    text: str,
    *,
    kind: str,
) -> None:
    summary = await summarize_text(text, kind=kind)
    if not summary:
        return
    # DB call is sync; hop to a thread so we don't block the loop.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _update_interaction_summary, interaction_id, summary)


def schedule_interaction_summary(
    interaction_id: str,
    *,
    content: Dict[str, Any],
    kind: str,
) -> None:
    """Fire-and-forget entrypoint called from the touchpoint ingest path.

    No-op unless :data:`HERMES_AUTO_SUMMARIZE` is truthy. Skipped silently
    if ``content`` already has a non-empty ``summary`` or if the body
    can't be extracted.
    """
    if not _auto_enabled():
        return
    if not isinstance(content, dict):
        return
    if content.get("summary"):
        return

    # Pull the best free-text surface from the content payload.
    text = ""
    for key in ("text", "body", "transcript", "message"):
        v = content.get(key)
        if isinstance(v, str) and v.strip():
            text = v
            break
    if not text:
        return

    # FastAPI runs sync route handlers (including adapter.ingest → emit)
    # in a thread pool, so there's no running loop here. Prefer attaching
    # to the running loop when present; otherwise spin up a short-lived
    # daemon thread with its own loop.
    coro_factory = lambda: _summarize_and_store(interaction_id, text, kind=kind)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro_factory())
        return
    except RuntimeError:
        pass

    def _run() -> None:
        try:
            asyncio.run(coro_factory())
        except Exception:
            logger.exception("summarize_thread_failed id=%s", interaction_id)

    threading.Thread(target=_run, name=f"summarize-{interaction_id[:8]}", daemon=True).start()
