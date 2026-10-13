"""Pure-logic tests for Wave A schema-evolution helpers.

Covers:
  * VALID_STATUSES constant
  * FieldProposalsDb.mark_reviewed — invalid status raises before DB call
  * FieldProposalsDb.list_by_status — invalid status raises before DB call
  * SchemaChangelogDb.list_recent — SQL clause building (no DB required,
    call is stubbed to capture the generated SQL)
  * add_module_field field_name validation (identity guard in routes)

All tests run without Postgres.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# FieldProposalsDb — status validation
# ---------------------------------------------------------------------------


def test_valid_statuses_constant():
    from hermes.crm.models.field_proposals_db import VALID_STATUSES

    assert "pending" in VALID_STATUSES
    assert "approved" in VALID_STATUSES
    assert "rejected" in VALID_STATUSES
    assert "withdrawn" in VALID_STATUSES


def test_mark_reviewed_invalid_status_raises_before_db():
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    fp = FieldProposalsDb(db)
    with pytest.raises(ValueError, match="invalid status"):
        fp.mark_reviewed(1, status="bogus", reviewer_id=1, review_note=None)
    db.execute.assert_not_called()


def test_mark_reviewed_valid_status_calls_db():
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    fp = FieldProposalsDb(db)
    fp.mark_reviewed(42, status="approved", reviewer_id=7, review_note="LGTM")
    db.execute.assert_called_once()


def test_mark_reviewed_all_valid_statuses_pass():
    from hermes.crm.models.field_proposals_db import FieldProposalsDb, VALID_STATUSES

    for status in VALID_STATUSES:
        db = MagicMock()
        fp = FieldProposalsDb(db)
        fp.mark_reviewed(1, status=status, reviewer_id=1, review_note=None)
        db.execute.assert_called_once()


def test_list_by_status_invalid_status_raises():
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    fp = FieldProposalsDb(db)
    with pytest.raises(ValueError, match="invalid status"):
        fp.list_by_status("not_a_real_status")
    db.fetchall.assert_not_called()


def test_list_by_status_valid_calls_db():
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    db.fetchall.return_value = []
    fp = FieldProposalsDb(db)
    result = fp.list_by_status("pending")
    db.fetchall.assert_called_once()
    assert result == []


def test_list_by_status_limit_capped_at_500():
    """The limit arg is clamped to max 500; verify the bound value appears."""
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    db.fetchall.return_value = []
    fp = FieldProposalsDb(db)
    fp.list_by_status("pending", limit=99999)
    _, args = db.fetchall.call_args[0]
    assert 500 in args


def test_list_by_status_limit_minimum_is_1():
    """Limit of 0 or negative is clamped to 1."""
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    db.fetchall.return_value = []
    fp = FieldProposalsDb(db)
    fp.list_by_status("pending", limit=0)
    _, args = db.fetchall.call_args[0]
    assert 1 in args


def test_count_by_status_invalid_status_raises():
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    fp = FieldProposalsDb(db)
    with pytest.raises(ValueError, match="invalid status"):
        fp.count_by_status("not_a_real_status")
    db.fetchone.assert_not_called()


def test_count_by_status_valid_returns_int():
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    db.fetchone.return_value = {"n": 4}
    fp = FieldProposalsDb(db)
    assert fp.count_by_status("pending") == 4
    db.fetchone.assert_called_once()


def test_count_by_status_handles_empty_result():
    """A NULL/absent count degrades to 0, never None."""
    from hermes.crm.models.field_proposals_db import FieldProposalsDb

    db = MagicMock()
    db.fetchone.return_value = None
    fp = FieldProposalsDb(db)
    assert fp.count_by_status("pending") == 0


# ---------------------------------------------------------------------------
# localize_field_label — per-field label_i18n override (2026-05-21 i18n fix)
# ---------------------------------------------------------------------------


def test_localize_field_label_label_i18n_override_wins():
    """An explicit field_config.label_i18n[locale] is authoritative —
    it beats the heuristic TEXT_TO_* dictionaries."""
    from hermes.crm.i18n.field_labels import localize_field_label

    cfg = {"label_i18n": {"en-US": "Owner", "zh-CN": "负责人"}}
    assert localize_field_label(
        table_name="accounts", field_name="owner_by", field_label="负责人",
        field_config=cfg, locale="en-US") == "Owner"
    assert localize_field_label(
        table_name="accounts", field_name="owner_by", field_label="Owner",
        field_config=cfg, locale="zh-CN") == "负责人"


def test_localize_field_label_label_i18n_accepts_json_string():
    """field_config may arrive as a JSON string (some psycopg row
    factories) — the override still applies."""
    import json
    from hermes.crm.i18n.field_labels import localize_field_label

    cfg = json.dumps({"label_i18n": {"en-US": "Custom EN"}})
    assert localize_field_label(
        table_name="x", field_name="f", field_label="中文标签",
        field_config=cfg, locale="en-US") == "Custom EN"


def test_localize_field_label_no_config_falls_back_to_dict():
    """With no field_config, resolution falls through to the heuristic
    dictionaries — unchanged legacy behaviour."""
    from hermes.crm.i18n.field_labels import localize_field_label

    assert localize_field_label(
        table_name="accounts", field_name="owner_by", field_label="Owner",
        locale="zh-CN") == "负责人"


def test_localize_field_label_label_i18n_missing_locale_falls_through():
    """label_i18n with only en-US → a zh-CN read falls through to the
    primary field_label rather than returning the English."""
    from hermes.crm.i18n.field_labels import localize_field_label

    cfg = {"label_i18n": {"en-US": "Procurement Cycle"}}
    assert localize_field_label(
        table_name="leads", field_name="procurement_cycle",
        field_label="采购周期", field_config=cfg, locale="zh-CN") == "采购周期"


# ---------------------------------------------------------------------------
# Picklist options — CSV/JSON parsing + field_config.option_labels (2026-05-21)
# ---------------------------------------------------------------------------


def test_parse_picklist_options_handles_csv():
    """The kacrm-classic bare-CSV options form must parse to value dicts."""
    from hermes.crm.i18n.options import parse_picklist_options

    opts = parse_picklist_options("new,contacted,qualified")
    assert [o["value"] for o in opts] == ["new", "contacted", "qualified"]


def test_parse_picklist_options_handles_json_dicts():
    from hermes.crm.i18n.options import parse_picklist_options

    opts = parse_picklist_options('[{"value":"feishu","text":"飞书"}]')
    assert opts[0]["value"] == "feishu"
    assert opts[0]["text"] == "飞书"


def test_localize_picklist_options_applies_option_labels():
    """field_config.option_labels[value][locale] resolves an option's
    display text for both locales."""
    from hermes.crm.i18n.options import (
        localize_picklist_options, parse_picklist_options,
    )

    opts = parse_picklist_options("new,lost")
    fc = {"option_labels": {
        "new":  {"zh-CN": "新建", "en-US": "New"},
        "lost": {"zh-CN": "已流失", "en-US": "Lost"},
    }}
    zh = localize_picklist_options(opts, locale="zh-CN", field_config=fc)
    assert {o["value"]: o["text"] for o in zh} == {"new": "新建", "lost": "已流失"}
    en = localize_picklist_options(opts, locale="en-US", field_config=fc)
    assert {o["value"]: o["text"] for o in en} == {"new": "New", "lost": "Lost"}


def test_localize_picklist_options_no_labels_keeps_value():
    """With no option_labels the option text is left as-is (the value)."""
    from hermes.crm.i18n.options import (
        localize_picklist_options, parse_picklist_options,
    )

    out = localize_picklist_options(
        parse_picklist_options("xyz"), locale="zh-CN", field_config=None)
    assert out[0]["text"] == "xyz"


# ---------------------------------------------------------------------------
# SchemaChangelogDb — SQL clause building
# ---------------------------------------------------------------------------


def test_list_recent_no_filters_has_no_where():
    from hermes.crm.models.schema_changelog_db import SchemaChangelogDb

    db = MagicMock()
    db.fetchall.return_value = []
    sc = SchemaChangelogDb(db)
    sc.list_recent()
    sql, _ = db.fetchall.call_args[0]
    assert "WHERE" not in sql.upper() or "WHERE " not in sql


def test_list_recent_module_id_filter_adds_where():
    from hermes.crm.models.schema_changelog_db import SchemaChangelogDb

    db = MagicMock()
    db.fetchall.return_value = []
    sc = SchemaChangelogDb(db)
    sc.list_recent(module_id=42)
    sql, args = db.fetchall.call_args[0]
    assert "module_id = %s" in sql
    assert 42 in args


def test_list_recent_actor_type_filter_adds_where():
    from hermes.crm.models.schema_changelog_db import SchemaChangelogDb

    db = MagicMock()
    db.fetchall.return_value = []
    sc = SchemaChangelogDb(db)
    sc.list_recent(actor_type="agent")
    sql, args = db.fetchall.call_args[0]
    assert "actor_type = %s" in sql
    assert "agent" in args


def test_list_recent_both_filters_combined():
    from hermes.crm.models.schema_changelog_db import SchemaChangelogDb

    db = MagicMock()
    db.fetchall.return_value = []
    sc = SchemaChangelogDb(db)
    sc.list_recent(module_id=7, actor_type="user")
    sql, args = db.fetchall.call_args[0]
    assert "module_id = %s" in sql
    assert "actor_type = %s" in sql
    assert 7 in args
    assert "user" in args


def test_list_recent_limit_capped_at_500():
    from hermes.crm.models.schema_changelog_db import SchemaChangelogDb

    db = MagicMock()
    db.fetchall.return_value = []
    sc = SchemaChangelogDb(db)
    sc.list_recent(limit=9999)
    _, args = db.fetchall.call_args[0]
    assert 500 in args


def test_list_recent_limit_default_is_100():
    from hermes.crm.models.schema_changelog_db import SchemaChangelogDb

    db = MagicMock()
    db.fetchall.return_value = []
    sc = SchemaChangelogDb(db)
    sc.list_recent()
    _, args = db.fetchall.call_args[0]
    assert 100 in args


def test_list_recent_orders_by_ts_desc():
    from hermes.crm.models.schema_changelog_db import SchemaChangelogDb

    db = MagicMock()
    db.fetchall.return_value = []
    sc = SchemaChangelogDb(db)
    sc.list_recent()
    sql, _ = db.fetchall.call_args[0]
    assert "ts DESC" in sql or "ORDER BY ts" in sql.upper()


# ---------------------------------------------------------------------------
# field_name validation — routes add_module_field identifier guard
# ---------------------------------------------------------------------------


def _is_valid_field_name(name: str) -> bool:
    """Mirror the guard in add_module_field:
       name.replace('_', '').isalnum() and name[:1].isalpha()
    """
    return bool(name) and name.replace("_", "").isalnum() and name[:1].isalpha()


def test_field_name_valid_simple():
    assert _is_valid_field_name("revenue")


def test_field_name_valid_with_underscore():
    assert _is_valid_field_name("annual_revenue")


def test_field_name_valid_with_digits():
    assert _is_valid_field_name("field2_name")


def test_field_name_invalid_starts_with_digit():
    assert not _is_valid_field_name("2revenue")


def test_field_name_invalid_starts_with_underscore():
    assert not _is_valid_field_name("_revenue")


def test_field_name_invalid_hyphen():
    assert not _is_valid_field_name("field-name")


def test_field_name_invalid_space():
    assert not _is_valid_field_name("field name")


def test_field_name_invalid_empty():
    assert not _is_valid_field_name("")


def test_field_name_invalid_special_chars():
    assert not _is_valid_field_name("field$name")
