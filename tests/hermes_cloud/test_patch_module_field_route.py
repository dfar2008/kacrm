"""Tests for the PATCH /crm/modules/{m}/fields/{name} allowed_keys
expansion: is_searchable / is_readonly / default_value / field_config
become editable; field_name / field_type stay locked.

Pure-ish: the allowed_keys set is the contract under test. We assert
on the constant the route uses, kept module-level for testability.
"""
from __future__ import annotations


def test_patch_allowed_keys_includes_new_keys():
    from hermes_cloud.app.crm.routes import PATCH_FIELD_ALLOWED_KEYS

    assert PATCH_FIELD_ALLOWED_KEYS == {
        "field_label", "is_hidden", "is_required", "description",
        "is_searchable", "is_readonly", "default_value", "field_config",
    }


def test_patch_allowed_keys_excludes_identity_keys():
    from hermes_cloud.app.crm.routes import PATCH_FIELD_ALLOWED_KEYS

    assert "field_name" not in PATCH_FIELD_ALLOWED_KEYS
    assert "field_type" not in PATCH_FIELD_ALLOWED_KEYS
    assert "is_summary" not in PATCH_FIELD_ALLOWED_KEYS
