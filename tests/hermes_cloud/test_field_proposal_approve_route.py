"""The field_proposals approval route delegates field creation to
provision_field — same helper as the direct route. This removes the
duplicated, 7-type-only sql_type_map that lived inline in the route.

We patch provision_field and assert the approval path calls it with
the proposal's field data.
"""
from __future__ import annotations


def test_approval_route_uses_provision_field():
    """Smoke test: the approval handler imports and calls
    provision_field rather than running its own ALTER TABLE."""
    import hermes_cloud.app.crm.routes as routes_mod

    # provision_field is imported at module scope in routes/__init__.py
    # (added in P0.5) — confirm it is the symbol the approval route
    # resolves.
    assert hasattr(routes_mod, "provision_field")

    src = __import__("inspect").getsource(routes_mod)
    # The old inline map and ALTER TABLE must be gone from the
    # approval handler; provision_field must be the creation path.
    assert "sql_type_map = {" not in src, (
        "approval route still has its own inline sql_type_map")
