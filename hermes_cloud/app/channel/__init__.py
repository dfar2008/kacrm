"""IM channel infrastructure (Phase 4 One-Agent Unification).

This package holds the cloud-side channel layer that connects IM bridges
(飞书, iLink) and the SPA Copilot panel to the single hermes AIAgent.
The split:

* ``store`` — typed DAO around the 4 Phase 4 tables (alembic 0049-0052):
  user_channel_prefs, channel_push_log, channel_quick_replies,
  auto_drive_runs.
* ``dispatch`` (later) — routes inbound IM messages to either the
  approval flow, quick-slot resolver, or the Copilot send path.
* ``surface/`` (sibling, later) — entity injection + per-platform render.

See ``docs/2026-05-09-channel-charter-v2-plan.md`` for the full plan.
"""
