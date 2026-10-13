"""``GET /api/gateway/actions`` returns ``origin`` so the SPA can
implement Charter §1 渠道闭环 read-only display.

The dashboard SHOWS pending actions from every channel (operators
deserve global visibility) but only ACTS on rows whose
``origin.channel == "webui"`` — the SPA renders the others with a
disabled approve/reject button + banner pointing the operator to the
originating channel.

Without ``origin`` in the response the SPA can't make that decision
client-side, so it would either:
  a) try to approve and get a 409 from the dispatcher's cross-channel
     guard (Charter §1 enforced server-side, but with a confusing UX), or
  b) hide the rows entirely, breaking universal-visibility.

This test pins the column into the SELECT so a future cleanup pass
can't drop it without breaking the SPA.
"""
from __future__ import annotations

import inspect

from hermes_cloud.app.gateway import api as gateway_api


def test_list_actions_query_selects_origin():
    src = inspect.getsource(gateway_api.list_actions)
    # The SELECT lists columns explicitly; ``origin`` must be in the list
    # so the JSONB payload makes it back to the dashboard.
    assert "origin" in src, (
        "list_actions must SELECT the ``origin`` column — the SPA "
        "depends on it to enforce Charter §1 渠道闭环 client-side."
    )
