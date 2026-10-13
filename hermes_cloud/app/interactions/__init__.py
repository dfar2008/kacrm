"""Interactions — unified event stream (原语 ① in the hermes-crm plan).

Every subsystem that "something happened" — CRM writes, agent actions,
incoming feishu messages, completed workflows — funnels through
:func:`emit` here. Readers (timeline views, RL sample builders, Agent
context providers) then query the single ``interactions`` table instead
of stitching together per-module audit logs.

Public surface:

    from hermes_cloud.app.interactions import emit

    emit(
        kind="action",
        subjects=[{"module": "accounts", "id": 17}],
        actors=[{"type": "user", "id": 1}],
        content={"op": "update", "changed_fields": ["email"]},
        source="manual",
    )
"""
from __future__ import annotations

from hermes_cloud.app.interactions.emitter import emit

__all__ = ["emit"]
