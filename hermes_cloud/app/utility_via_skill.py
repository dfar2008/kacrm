"""Charter v2 §定律 4 compliance — run a utility-style LLM task through
the main agent + a markdown skill, instead of bypassing it with a
direct ``chat_with_tools`` call.

Used by ``insights/question_generator``, ``insights/brief_generator``,
and ``insights/scanners/coach_orphan_commitment``. The legacy direct-
LLM paths are removed in these modules — charter v2 §定律 4 is the
single source of truth in dev phase. If the main-agent call fails,
the caller's own fallback (template questions / scanner output) kicks
in, NOT a parallel direct-LLM path.

The helper packages:
  * envelope construction with ``SurfaceKind.AUTO_DRIVE`` (no inbox
    registration — utility runs are not conversations)
  * dispatch_to_hermes
  * tolerant JSON parsing (handles code-fenced or prose-prefixed output)

The skill markdown for each utility lives under
``hermes/skills/crm-{question-suggest,daily-brief,orphan-commitment}/SKILL.md``.
The main agent picks the right skill by reading the per-call instruction
text (``[utility:<name>] ...``) plus its ``when_to_use`` index.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class UtilitySkillError(Exception):
    """Raised when a utility-skill dispatch fails in a way the caller
    must surface (transport failure or non-2xx from hermes).

    Carries the underlying status (502 for transport, otherwise the
    downstream HTTP code) and a body excerpt so the cloud endpoint
    can re-raise as an HTTPException with the real reason. We keep
    ``run_utility_skill``'s default behavior (return ``None`` on error
    so direct callers can fall back) — opt-in to strict mode with
    ``raise_on_error=True``. See charter v2 §一系统: never silently
    convert downstream failures into "agent decided 0 results".
    """

    def __init__(self, *, status_code: int, reason: str, body: str = ""):
        super().__init__(f"utility-skill failed: status={status_code} reason={reason}")
        self.status_code = status_code
        self.reason = reason
        self.body = body


def parse_json_reply(raw: str) -> Any:
    """Best-effort JSON parsing of a main-agent reply.

    Main agent occasionally wraps JSON in ```json fences or adds a
    natural-language preamble — strip both before parsing. Returns
    ``None`` on unparseable input so callers can fall back without
    raising.
    """
    text = (raw or "").strip()
    if not text:
        return None
    # Strip fences (``` and ```json).
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    # If the model added prose, grab the first top-level JSON block.
    if text and text[0] not in "[{":
        match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if match:
            text = match.group(1)
    try:
        return json.loads(text)
    except Exception:
        logger.info("utility_via_skill: JSON parse failed: %r", text[:200])
        return None


async def run_utility_skill(
    *,
    app: Any,
    user_id: int,
    skill_name: str,
    instruction: str,
    timeout_sec: float = 60.0,
    response_format_json: bool = True,
    raise_on_error: bool = False,
) -> Optional[Any]:
    """Send ``instruction`` to the user's main agent with a hint to
    load ``skill_name``. Returns the parsed reply (JSON if
    ``response_format_json``, else raw text).

    Default behavior (``raise_on_error=False``): returns ``None`` on
    transport / non-2xx / unparseable output. Direct callers (insight
    scanners, brief generator) want this so they can fall back to
    template output.

    Strict mode (``raise_on_error=True``): raises ``UtilitySkillError``
    on transport failure or non-2xx. Empty/unparseable reply still
    returns ``None`` (a legitimate "agent decided no result" answer
    for skills like ``crm-daily-coach`` whose 0-card output is valid).
    The cloud's ``/internal/utility/run-skill`` endpoint uses this so
    LLM/transport failures surface as 5xx instead of being silently
    dressed up as a 200 OK + null reply.

    The ``instruction`` should be a self-contained brief that the
    skill's ``when_to_use`` index can match (e.g. "[utility:question_suggest]
    Generate 3 questions for record …").
    """
    try:
        from hermes_cloud.app.surface import HermesRequestEnvelope, SurfaceKind
        from hermes_cloud.app.proxy.dispatch import dispatch_to_hermes
    except Exception:
        logger.warning("utility_via_skill: surface/proxy unavailable")
        if raise_on_error:
            raise UtilitySkillError(
                status_code=500,
                reason="surface_or_proxy_unavailable",
            )
        return None

    session_id = f"utility-{skill_name}-{uuid.uuid4().hex[:12]}"
    envelope = HermesRequestEnvelope(
        # AUTO_DRIVE keeps the call OUT of the inbox (charter v2 §定律 4
        # — utility runs are system-driven, not conversations).
        surface=SurfaceKind.AUTO_DRIVE,
        user_id=int(user_id),
        session_id=session_id,
        messages=[
            {
                "role": "user",
                "content": (
                    f"[utility:{skill_name}] {instruction}\n\n"
                    f"Load skill `{skill_name}` for the output contract. "
                    f"Reply with the JSON described in the skill, nothing else."
                    if response_format_json else
                    f"[utility:{skill_name}] {instruction}\n\n"
                    f"Load skill `{skill_name}` for the output contract."
                ),
            },
        ],
        headers={
            "X-Hermes-Origin-Channel": "utility",
            "X-Hermes-Session-Id": session_id,
        },
    )

    # hermes subprocess requires an Authorization header — use the
    # cloud's internal-key (same as other internal cloud→hermes calls).
    try:
        bearer = str(app.state.config.internal_key or "")
    except Exception:
        bearer = ""

    try:
        result = await dispatch_to_hermes(
            app=app,
            envelope=envelope,
            bearer_key=bearer,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        logger.warning(
            "utility_via_skill: dispatch failed skill=%s user=%s err=%s",
            skill_name, user_id, exc,
        )
        if raise_on_error:
            raise UtilitySkillError(
                status_code=502,
                reason=f"dispatch_failed: {exc}",
            ) from exc
        return None

    if result.status_code >= 400:
        logger.warning(
            "utility_via_skill: dispatch non-2xx skill=%s status=%s body=%r",
            skill_name, result.status_code, result.raw_body[:300],
        )
        if raise_on_error:
            # raw_body is bytes from httpx — decode for JSON-safe transit.
            body_str = ""
            if result.raw_body:
                if isinstance(result.raw_body, bytes):
                    body_str = result.raw_body.decode("utf-8", errors="replace")[:1000]
                else:
                    body_str = str(result.raw_body)[:1000]
            raise UtilitySkillError(
                status_code=result.status_code,
                reason="hermes_returned_error",
                body=body_str,
            )
        return None

    text = (result.reply_text or "").strip()
    if not text:
        logger.warning(
            "utility_via_skill: empty reply_text skill=%s status=%s raw_body=%r",
            skill_name, result.status_code, result.raw_body[:500],
        )
        return None
    if response_format_json:
        return parse_json_reply(text)
    return text


__all__ = [
    "UtilitySkillError",
    "parse_json_reply",
    "run_utility_skill",
]
