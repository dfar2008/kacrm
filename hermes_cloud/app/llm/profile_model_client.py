"""Profile-default model resolver + lightweight chat client.

This module is intentionally tiny and synchronous so call sites in
agent/memory ingestion (which are currently sync) can consume it
without event-loop plumbing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from hermes.crm.db import DbWrapper, db_session

logger = logging.getLogger(__name__)


@dataclass
class ProfileModelConfig:
    model: str
    base_url: str
    api_key: Optional[str]
    source: str  # "profile" | "user_default"


def _is_populated(row: Optional[dict[str, Any]]) -> bool:
    return bool(row and (row.get("model") or row.get("base_url") or row.get("api_key")))


def _looks_like_trained_model(model: str) -> bool:
    s = (model or "").strip().lower()
    if not s:
        return False
    # We intentionally avoid RL adapter names during the no-train phase.
    return (
        s.startswith("ad_")
        or "grpo" in s
        or s.startswith("crm_grpo")
        or s.startswith("crm_sft")
    )


def _chat_url(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b:
        return ""
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1"):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


def resolve_profile_default_model(
    *,
    user_id: int,
    profile_id: Optional[str] = None,
    db: Optional[DbWrapper] = None,
) -> Optional[ProfileModelConfig]:
    """Resolve profile model config with default-profile fallback.

    Policy:
      1) prefer profile_models(profile_id)
      2) fallback to user_models(user_id-as-string)
      3) reject likely RL adapter names in no-train phase
    """

    def _run(conn: DbWrapper) -> Optional[ProfileModelConfig]:
        prof_row = None
        if profile_id:
            try:
                prof_row = conn.fetchone(
                    "SELECT model, base_url, api_key FROM profile_models WHERE profile_id = %s LIMIT 1",
                    (str(profile_id),),
                )
            except Exception:
                prof_row = None
        user_row = None
        try:
            user_row = conn.fetchone(
                "SELECT model, base_url, api_key FROM user_models WHERE user_id = %s LIMIT 1",
                (str(user_id),),
            )
        except Exception:
            user_row = None

        row = None
        source = ""
        if _is_populated(prof_row):
            row = prof_row
            source = "profile"
        elif _is_populated(user_row):
            row = user_row
            source = "user_default"
        if not row:
            return None

        model = str(row.get("model") or "").strip()
        base_url = str(row.get("base_url") or "").strip()
        api_key = row.get("api_key")
        if not model or not base_url:
            return None
        if _looks_like_trained_model(model):
            logger.info("profile_model_client: skip trained model=%s source=%s", model, source)
            return None
        return ProfileModelConfig(
            model=model,
            base_url=base_url,
            api_key=str(api_key) if api_key else None,
            source=source,
        )

    if db is not None:
        return _run(db)
    with db_session() as owned:
        return _run(owned)


def chat_once(
    *,
    cfg: ProfileModelConfig,
    messages: List[Dict[str, Any]],
    max_tokens: int = 220,
    temperature: float = 0.2,
    timeout_sec: float = 15.0,
) -> Optional[str]:
    """Run one OpenAI-compatible chat call and return text content."""
    url = _chat_url(cfg.base_url)
    if not url:
        return None
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    payload: Dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
    }
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "profile_model_client: http_%s model=%s body=%s",
                resp.status_code, cfg.model, resp.text[:300],
            )
            return None
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for p in content:
                if isinstance(p, dict) and p.get("type") in ("text", "output_text"):
                    t = p.get("text") or p.get("content")
                    if isinstance(t, str):
                        parts.append(t)
            return "\n".join(parts).strip() if parts else None
        return None
    except Exception as exc:
        logger.warning("profile_model_client: transport_failed model=%s err=%r", cfg.model, exc)
        return None


def chat_stream(
    *,
    cfg: ProfileModelConfig,
    messages: List[Dict[str, Any]],
    max_tokens: int = 1024,
    temperature: float = 0.3,
    connect_timeout: float = 10.0,
    read_timeout: float = 300.0,
):
    """Run a streaming OpenAI-compatible chat call.

    Generator yielding text deltas (each = one chunk's
    ``choices[0].delta.content``). Network errors raise; callers should
    wrap in try/except. Used by dashboards chat (spec §6.d).

    Note: returns the raw text fragments. Higher layers concatenate
    + line-buffer + JSON-parse op frames.

    Timeout split: ``connect_timeout`` stays short so a dead host fails
    fast, but ``read_timeout`` is generous — it is the max gap allowed
    between socket reads, and reasoning models (MiniMax-M2, DeepSeek-R1)
    can sit silent for minutes burning ``<think>`` tokens before the
    first byte. A single scalar httpx timeout conflated the two and
    killed reasoning-model streams at 60s with "The read operation
    timed out". The stream is naturally bounded by the ``[DONE]``
    sentinel + ``max_tokens``; ``read_timeout`` is only a hung-provider
    safety net.
    """
    url = _chat_url(cfg.base_url)
    if not url:
        return
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    payload: Dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": True,
    }
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=connect_timeout,
        pool=connect_timeout,
    )
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", errors="replace")[:300]
                logger.warning(
                    "profile_model_client.stream: http_%s model=%s body=%s",
                    resp.status_code, cfg.model, body,
                )
                return
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                # httpx iter_lines may yield str or bytes depending on
                # response encoding; normalize.
                line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload_str = line[5:].strip()
                if payload_str == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload_str)
                except Exception:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content
                elif isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict):
                            t = p.get("text") or p.get("content")
                            if isinstance(t, str) and t:
                                yield t


def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        lines = [ln for ln in cleaned.splitlines() if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None
