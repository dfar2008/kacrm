"""``dispatch_to_hermes`` — single entry for forwarding a chat-completion
to the user's per-profile hermes subprocess.

Replaces the pattern where SPA-side endpoints would call cloud's
``chat_with_tools`` directly (which broke Charter §简化 by running a
parallel LLM loop), AND lets ``/v1/chat/completions`` handler share the
same plumbing instead of every entry point re-implementing
profile-manager → http-client → response-parsing → usage-capture.

Scope:

* Non-streaming. SSE fan-out stays in ``main._proxy_stream`` —
  request-bound and doesn't fit the envelope/result shape.
* Captures LLM usage centrally — every call writes one
  ``record_llm_usage`` row with the surface name baked into the
  ``purpose`` so analytics can split chatbox / spa_copilot / feishu /
  iLink / auto_drive without per-caller telemetry plumbing.
* Profile resolution: if ``profile_id`` is not provided, the user's
  default profile is used (``profile_store.ensure_default_profile``).
  Mirrors :func:`main._resolve_profile_id`'s default branch.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from hermes_cloud.app.surface import HermesRequestEnvelope

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Outcome of one non-streaming round-trip to hermes."""

    status_code: int = 200
    raw_body: bytes = b""
    reply_text: str = ""
    tool_executions: List[Dict[str, Any]] = field(default_factory=list)
    response_headers: Dict[str, str] = field(default_factory=dict)
    session_id: str = ""
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


async def dispatch_to_hermes(
    *,
    app: Any,
    envelope: HermesRequestEnvelope,
    profile_id: Optional[str] = None,
    body_extras: Optional[Dict[str, Any]] = None,
    bearer_key: str = "",
    timeout_sec: float = 60.0,
    record_usage: bool = True,
) -> DispatchResult:
    """Forward ``envelope`` to the user's hermes subprocess.

    Pulls runtime collaborators from ``app.state``:

    * ``profile_manager`` — to ensure-running + acquire the local port
    * ``http_client`` — shared async httpx client
    * ``profile_store`` — for default-profile resolution

    The envelope's ``headers`` (Origin-Channel from patch 20, Surface-ACL
    from patch 27, Session-Id from §4.7) flow straight through.
    Caller is free to merge additional fields into the request body
    via ``body_extras`` (e.g. ``{"tools": [...], "stream": False}``);
    ``messages`` always come from the envelope and are NOT overridable.
    """
    profile_manager = app.state.profile_manager
    http_client: httpx.AsyncClient = app.state.http_client
    profile_store = app.state.profile_store

    # Resolve profile (mirror main._resolve_profile_id default branch).
    if profile_id is None:
        default = profile_store.ensure_default_profile(user_id=str(envelope.user_id))
        profile_id = default.id
    try:
        from hermes_cloud.app.main import _sync_config_to_profile  # local
        _sync_config_to_profile(str(envelope.user_id), profile_id)
    except Exception as exc:
        logger.debug("dispatch_to_hermes: config sync skipped: %s", exc)

    port = await profile_manager.ensure_running(
        profile_id, user_id=str(envelope.user_id),
    )
    profile_manager.touch(profile_id)

    url = f"http://127.0.0.1:{port}/v1/chat/completions"

    # Build OpenAI-format body. Caller's body_extras may add ``tools``,
    # ``temperature``, ``model``, etc. — but ``messages`` always come
    # from the envelope.
    body: Dict[str, Any] = dict(body_extras or {})
    body["messages"] = list(envelope.messages)
    body.setdefault("model", body.get("model", "default"))
    body["stream"] = False  # this helper is non-streaming

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if bearer_key:
        headers["Authorization"] = f"Bearer {bearer_key}"
    # Charter v2 §定律 4 — propagate cloud user_id so hermes can stamp
    # agent_sessions.user_id correctly (patch 38 reads this header
    # into the SESSION_USER_ID ContextVar).
    if envelope.user_id:
        headers["X-Hermes-Cloud-User-Id"] = str(envelope.user_id)
    # Envelope headers (X-Hermes-Origin-Channel, X-Hermes-Surface-ACL,
    # X-Hermes-Session-Id) win over anything we set above.
    headers.update(envelope.headers)
    # Conversation-Id mirrors session-id; some hermes paths look at one,
    # some at the other.
    headers.setdefault("X-Conversation-Id", envelope.session_id)

    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    try:
        resp = await http_client.post(
            url, headers=headers, content=raw_body,
            timeout=httpx.Timeout(timeout_sec),
        )
    except Exception as exc:
        logger.warning(
            "dispatch_to_hermes transport error user=%s err=%s exc_type=%s",
            envelope.user_id, exc, type(exc).__name__,
        )
        return DispatchResult(
            status_code=503,
            raw_body=json.dumps({"error": {
                "message": f"hermes transport: {exc}", "code": "process_unavailable",
            }}).encode("utf-8"),
            session_id=envelope.session_id,
        )

    out = DispatchResult(
        status_code=resp.status_code,
        raw_body=resp.content,
        response_headers=dict(resp.headers),
        session_id=envelope.session_id,
    )

    if resp.status_code < 400:
        try:
            data = json.loads(resp.content or b"{}")
        except Exception:
            return out  # malformed JSON; surface as-is

        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            out.reply_text = _extract_text_content(msg)
            out.tool_executions = _extract_tool_executions(msg)
        out.model = str(data.get("model") or "")
        out.usage = data.get("usage") or {}

        # Charter §一原语 ① — uniform LLM usage capture so analytics
        # can split per-surface without each caller wiring telemetry.
        if record_usage and out.usage:
            try:
                from hermes_cloud.app.insights.llm_usage import record_llm_usage
                record_llm_usage(
                    user_id=int(envelope.user_id),
                    purpose=f"surface:{envelope.surface.value}",
                    msg={"__usage": out.usage, "__model": out.model or "unknown"},
                    session_id=envelope.session_id,
                )
            except Exception:
                logger.debug("dispatch_to_hermes usage record failed", exc_info=True)

    return out


def _extract_text_content(msg: Dict[str, Any]) -> str:
    """Pull the visible reply text out of an OpenAI-style message dict.
    Handles both string ``content`` and list-of-parts (some Anthropic-
    compatible providers use the latter)."""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: List[str] = []
        for p in c:
            if isinstance(p, dict) and p.get("type") in ("text", "output_text"):
                t = p.get("text") or p.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts).strip()
    return ""


def _extract_tool_executions(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize ``tool_calls`` to ``[{id, name, args}]``. Tool *results*
    aren't here — they're produced by hermes' own dispatch and surface
    in subsequent turns of a multi-round agent loop. This helper returns
    only the calls hermes emitted in the just-received turn."""
    out: List[Dict[str, Any]] = []
    tcs = msg.get("tool_calls")
    if isinstance(tcs, list):
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            raw_args = fn.get("arguments")
            if raw_args is None:
                raw_args = tc.get("arguments") or tc.get("input") or "{}"
            args: Dict[str, Any] = {}
            if isinstance(raw_args, dict):
                args = raw_args
            elif isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                    args = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    args = {}
            if name:
                out.append({
                    "id": tc.get("id") or f"call_{len(out)}",
                    "name": name,
                    "args": args,
                })
    return out
