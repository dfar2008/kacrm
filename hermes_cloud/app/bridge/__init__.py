"""Bridge — the four hermes↔CRM connection points.

Per ``docs/2026-04-15-hermes-crm-implementation-plan.md`` §6, the bridge
is the thin layer that lets hermes (agent runtime, dashboards, feishu
subprocesses) and CRM (routes, permissions, record writes) speak a
single identity / context / tool / event vocabulary without either side
owning the other.

Phase 1d lands the first of those four: :mod:`identity` (§6.1). The
remaining three — ``context`` (§6.2), ``tool_binding`` (§6.3),
``events`` (§6.4) — arrive in later phases.
"""
