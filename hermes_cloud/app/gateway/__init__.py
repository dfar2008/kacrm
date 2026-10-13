"""SPA-only Action Gateway HTTP routes — slim package after Charter v2 Phase 4b'.1.c.

The Action Gateway implementation (dispatcher, record_state, executors,
channel_reply, notifiers, text_commands, text_notifier, im_push) moved
to ``hermes/action_gateway/``. The only thing still hosted here is the
SPA HTTP route layer in :mod:`.api`, which depends on cloud-side
``auth`` / ``config`` and therefore stays in cloud.

For the actual gateway implementation, import from ``hermes.action_gateway.*``.

Importing ``hermes.action_gateway`` triggers executor registration as a
side effect (see ``hermes/action_gateway/__init__.py``).
"""
from __future__ import annotations
