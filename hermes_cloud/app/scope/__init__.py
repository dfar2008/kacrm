"""Phase 5.1 — team scope query helpers.

Public entry: :func:`viewable_user_ids`. Used by:
  · cloud crm REST routes (legacy callers _collect_viewable_user_ids)
  · cloud surface/proxy layer (sets X-Hermes-Scope header for hermes)
  · cloud insights / dashboard (5.6)
  · auto_drive.run_for_team (5.3)
"""
from hermes_cloud.app.scope.team_scope import (
    ScopeKind,
    viewable_user_ids,
)

__all__ = ["ScopeKind", "viewable_user_ids"]
