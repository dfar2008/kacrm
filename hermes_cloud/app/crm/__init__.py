"""CRM SPA-only HTTP routes — slim package after Charter v2 Phase 4b'.1.b.

The CRM ORM, models, save/edit/list/detail query helpers, approval engine,
projections, workflow runtime, i18n data, and Memory Substrate
implementation moved to ``hermes/crm/`` (and the agent-tool dispatch
moved to ``hermes/tools/crm/dispatch.py``). The only thing still
hosted here is the SPA HTTP route layer in :mod:`.routes`, which
depends on cloud-side ``auth`` / ``bridge.context`` / ``config`` and
therefore stays in cloud.

For everything else, import from ``hermes.crm.*`` directly.
"""
from __future__ import annotations
