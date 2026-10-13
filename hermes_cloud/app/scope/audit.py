"""audit_log writer — Phase 5.5 (plan v1.2 §4.6).

Single canonical entry: :func:`record_scope_access`. Called by:
  · `surface.inject_context` when scope != 'self'
  · `auto_drive.run_for_team`
  · 5.6 dashboard SQL aggregate handlers
  · cloud crm REST list endpoints when caller is a manager

Failure-isolated: never raises into the read path. If the audit row
fails to insert (e.g. PG temporarily down), we log+continue —
the read still serves, but a missing audit row is itself a signal
that should surface in the dashboard's "audit gaps" view.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from hermes.crm.db import DbWrapper

logger = logging.getLogger(__name__)


def record_scope_access(
    db: DbWrapper, *,
    caller_id: int,
    scope_kind: str,
    resource_kind: str,
    resource_filter: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert one audit_log row. Best-effort — never raises.

    Caller passes:
      · ``scope_kind`` ∈ {'self','team','org'} (self is recorded too;
        keeps the audit trail homogeneous and lets analytics tell
        "this user only ever queries self" from "this user often
        elevates to team").
      · ``resource_kind`` short token: 'crm_search' / 'team_brief' /
        'dashboard_ai_calendar' / 'list_records' / etc.
      · ``resource_filter`` arbitrary JSON describing the actual
        SQL filter applied (module name, date range, etc.). Lets
        compliance replay "what did the manager actually see" by
        re-running the filter at audit time.
    """
    try:
        db.execute(
            """
            INSERT INTO audit_log
              (caller_id, scope_kind, resource_kind, resource_filter)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (
                int(caller_id),
                str(scope_kind),
                str(resource_kind),
                json.dumps(resource_filter or {}, ensure_ascii=False),
            ),
        )
    except Exception:
        logger.warning(
            "audit_log insert failed caller=%s scope=%s kind=%s",
            caller_id, scope_kind, resource_kind, exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass


__all__ = ["record_scope_access"]
