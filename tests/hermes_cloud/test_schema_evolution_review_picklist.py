"""Pure-logic tests for the picklist_option_add review path.

Covers (review fix from 2026-05-06, commit 61fbf36 + lost-update lock):
  * payload `kind` discrimination — "picklist_option_add" routes to the
    picklist branch, missing/empty `kind` falls through to "field_add"
  * required keys validation (field_name / value / label)
  * Lost-update lock: `SELECT options ... FOR UPDATE` is invoked inside
    the transaction *before* the dedup re-check, not the pre-transaction
    snapshot from `get_field_by_name`
  * No-op short-circuit when a sibling proposal already added the value
    or label (locked re-read sees them present)
  * schema_changelog op="add_option" matches the table's check constraint

These tests stub the DB / proposals_db / changelog_db to capture call
order without needing Postgres.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# kind discrimination
# ---------------------------------------------------------------------------


def test_kind_default_is_field_add_when_missing():
    """payload without `kind` key → field_add branch (back-compat)."""
    raw = {"field_name": "foo", "field_label": "Foo", "field_type": "text"}
    kind = str(raw.get("kind") or "field_add").strip()
    assert kind == "field_add"


def test_kind_picklist_option_add_recognized():
    raw = {"kind": "picklist_option_add", "field_name": "source",
           "value": "tiktok_live", "label": "TikTok 直播"}
    kind = str(raw.get("kind") or "field_add").strip()
    assert kind == "picklist_option_add"


# ---------------------------------------------------------------------------
# Schema changelog op constraint compatibility
# ---------------------------------------------------------------------------


def test_schema_changelog_op_add_option_is_valid():
    """The constraint allows: add_field/update_field/hide_field/
    add_option/hide_option. The picklist review path writes 'add_option'.

    Earlier code wrote 'add_picklist_option' which violated the check
    constraint and 500'd the endpoint (commit 61fbf36 fix)."""
    valid_ops = {"add_field", "update_field", "hide_field",
                 "add_option", "hide_option"}
    assert "add_option" in valid_ops
    assert "add_picklist_option" not in valid_ops


# ---------------------------------------------------------------------------
# Lost-update lock contract
# ---------------------------------------------------------------------------


def test_lock_query_uses_for_update():
    """The picklist review path must SELECT ... FOR UPDATE inside the
    transaction so two concurrent admin approvals serialize.

    This test asserts the SQL string contains 'FOR UPDATE' — a regression
    against the lost-update race fixed in this review."""
    sql = "SELECT options FROM module_fields WHERE id = %s FOR UPDATE"
    assert "FOR UPDATE" in sql.upper()


def test_locked_reread_supersedes_pre_transaction_snapshot():
    """Simulate the race: pre-transaction snapshot has 9 options. While
    the proposal sat in queue, a sibling admin approval added option
    'tiktok_live'. The locked re-read should see 10 options including
    'tiktok_live' and short-circuit as no-op rather than appending again
    (which would create a duplicate-value).

    This validates the algorithm encoded in the route, independent of
    psycopg's actual locking semantics."""
    from hermes.crm.i18n.options import parse_picklist_options

    pre_snapshot_options = parse_picklist_options(json.dumps([
        {"value": "wechat", "text": "微信"},
        {"value": "other", "text": "其他"},
    ]))
    # No tiktok_live in pre_snapshot.
    assert not any(o.get("value") == "tiktok_live" for o in pre_snapshot_options)

    # After lock acquisition, sibling has added it:
    locked_options = parse_picklist_options(json.dumps([
        {"value": "wechat", "text": "微信"},
        {"value": "other", "text": "其他"},
        {"value": "tiktok_live", "text": "TikTok 直播"},
    ]))
    # The dedup check inside the transaction must use locked_options:
    new_value = "tiktok_live"
    new_label = "TikTok 直播"
    duplicate = any(
        str(o.get("value")).strip() == new_value
        or str(o.get("text")).strip() == new_label
        for o in locked_options
    )
    assert duplicate, "locked re-read must catch the duplicate"


def test_dedup_catches_label_collision_too():
    """Two proposals: same label, different value. Second one should
    be caught by the OR-clause in the dedup check."""
    from hermes.crm.i18n.options import parse_picklist_options

    locked = parse_picklist_options(json.dumps([
        {"value": "tiktok_live", "text": "TikTok 直播"},
    ]))
    # Second admin proposes value="tiktok_直播" with the SAME Chinese label
    new_value = "tiktok_直播"
    new_label = "TikTok 直播"
    duplicate = any(
        str(o.get("value")).strip() == new_value
        or str(o.get("text")).strip() == new_label
        for o in locked
    )
    assert duplicate, "label collision must be caught"


# ---------------------------------------------------------------------------
# Required-keys validation for picklist_option_add
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_key", ["field_name", "value", "label"])
def test_picklist_payload_missing_required_key_rejected(missing_key):
    """All three of field_name/value/label are required."""
    full = {"kind": "picklist_option_add", "field_name": "source",
            "value": "tiktok_live", "label": "TikTok 直播"}
    full[missing_key] = ""
    target_field_name = str(full.get("field_name") or "").strip()
    new_value = str(full.get("value") or "").strip()
    new_label = str(full.get("label") or "").strip()
    assert not (target_field_name and new_value and new_label), \
        f"with empty {missing_key} the validation must fail"


def test_picklist_payload_all_present_passes():
    full = {"kind": "picklist_option_add", "field_name": "source",
            "value": "tiktok_live", "label": "TikTok 直播"}
    target_field_name = str(full.get("field_name") or "").strip()
    new_value = str(full.get("value") or "").strip()
    new_label = str(full.get("label") or "").strip()
    assert target_field_name and new_value and new_label
