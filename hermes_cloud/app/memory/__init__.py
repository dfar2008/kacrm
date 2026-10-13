"""SPA-only Memory HTTP routes — slim package after Charter v2 Phase 4b'.1.c.

The Memory Substrate implementation (embedder, entities, ingest,
normalize, provenance, quota, redaction, vector_store) moved to
``hermes/memory/``. The only thing still hosted here is the SPA
HTTP route layer in :mod:`.api`, which depends on cloud-side
``auth`` / ``config`` and therefore stays in cloud.

For the actual memory implementation, import from ``hermes.memory.*``.
"""
from __future__ import annotations
