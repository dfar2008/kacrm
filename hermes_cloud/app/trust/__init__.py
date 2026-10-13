"""SPA-only Trust HTTP routes — slim package after Charter v2 Phase 4b'.1.c.

The Trust Engine implementation (engine, auto_adjust) moved to
``hermes/trust/``. The only thing still hosted here is the SPA
HTTP route layer in :mod:`.api`, which depends on cloud-side
``auth`` / ``config`` and therefore stays in cloud.

For the actual trust implementation, import from ``hermes.trust.*``.
"""
from __future__ import annotations
