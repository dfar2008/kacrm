"""Cloud surface layer (Phase 4 One-Agent Unification, Week 1 Day 6-7).

Thin abstraction connecting IM bridges (飞书 / iLink) and the SPA Copilot
panel to the single hermes AIAgent. Per plan §4.1 the surface layer is
deliberately stateless — it does only:

* :func:`inject_context` — wrap user text + record context into the
  hermes-bound message list, and emit the matching ``X-Hermes-*`` headers
  (origin channel, surface ACL via patch 27, session-id per §4.7).
* :func:`render_reply` — format hermes' reply text for the surface
  (markdown for SPA, interactive card for 飞书, plain text + numeric
  slots for iLink).
* :func:`surface_acl_for` — per-surface tool allowlist that patch 27
  enforces on the hermes side.
* :func:`derive_session_id` — §4.7 day-session rule so SPA / 飞书 / iLink
  share the same hermes session across the day.

What this module is NOT:

* Not the LLM caller — that's hermes via ``/v1/chat/completions``.
* Not stateful — no per-session memory, no caching of conversations.
  Caching of expensive LLM utility outputs (record_question_cache,
  daily_brief_cache) lives in ``app/insights/`` and stays there.
* Not the auto_drive engine (that's Week 3).
"""
from hermes_cloud.app.surface.surface import (
    HermesRequestEnvelope,
    SurfaceKind,
    inject_context,
    render_reply,
    surface_acl_for,
)
from hermes_cloud.app.surface.session import derive_session_id

__all__ = [
    "HermesRequestEnvelope",
    "SurfaceKind",
    "inject_context",
    "render_reply",
    "surface_acl_for",
    "derive_session_id",
]
