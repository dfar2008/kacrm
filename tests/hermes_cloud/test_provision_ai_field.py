"""Tests for provision_field ai_fill integration.

Mirrors the MagicMock DbWrapper pattern from test_field_provisioning_provision.py.
No live DB required.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from hermes_cloud.app.crm.field_provisioning import provision_field


def _make_db() -> MagicMock:
    db = MagicMock()

    @contextmanager
    def _txn():
        yield

    db.transaction.side_effect = _txn
    db.id.return_value = 42  # field_id returned by insert_field
    return db


_MODULE = {"id": 5, "name": "contacts", "table_name": "contacts"}
_ACTOR = {"id": 1, "username": "admin"}

_AI_FILL_PICKLIST = {
    "enabled": True,
    # 4 模式体系:picklist 必须显式 mode=classify(validate 缺省 prompt,
    # 而 prompt 只允许 text/number/currency)。output_spec 已退役。
    "mode": "classify",
    "mission": "Classify the ICP level of this contact.",
    "output_type": "picklist",
    "inputs": {"fields": []},
}


def _executed_sql(db: MagicMock) -> list[str]:
    return [c[0][0] for c in db.execute.call_args_list]


# ---------------------------------------------------------------------------
# Test 1: ai_fill enabled → ai_meta JSONB column added
# ---------------------------------------------------------------------------

def test_ai_field_creates_ai_meta_column():
    db = _make_db()
    # patch ModuleFieldsDb.get_module_fields to return empty list (fresh module)
    with patch(
        "hermes_cloud.app.crm.field_provisioning.ModuleFieldsDb"
    ) as MockMFDb:
        mock_instance = MagicMock()
        mock_instance.get_module_fields.return_value = []
        MockMFDb.return_value = mock_instance

        provision_field(
            db,
            _MODULE,
            {
                "field_name": "icp_level",
                "field_label": "ICP Level",
                "field_type": "picklist",
                "options": "High,Medium,Low",
                "field_config": {"ai_fill": _AI_FILL_PICKLIST},
            },
            _ACTOR,
            reason=None,
        )

    sql_stmts = _executed_sql(db)
    sql_all = " ".join(sql_stmts)

    # The regular column must be created
    assert 'ADD COLUMN IF NOT EXISTS "icp_level"' in sql_all
    # The ai_meta JSONB column must also be created
    assert "ai_meta JSONB" in sql_all


# ---------------------------------------------------------------------------
# Test 2: invalid ai_fill (mode/field_type mismatch) → HTTPException 400
# ---------------------------------------------------------------------------

def test_invalid_ai_fill_rejected():
    db = _make_db()
    bad_ai_fill = {
        "enabled": True,
        "mission": "Classify the ICP level.",
        # mode 缺省 prompt,不允许用在 picklist 字段上 → 400
        "output_type": "number",
        "inputs": {"fields": []},
    }

    with patch(
        "hermes_cloud.app.crm.field_provisioning.ModuleFieldsDb"
    ) as MockMFDb:
        mock_instance = MagicMock()
        mock_instance.get_module_fields.return_value = []
        MockMFDb.return_value = mock_instance

        with pytest.raises(HTTPException) as exc:
            provision_field(
                db,
                _MODULE,
                {
                    "field_name": "icp_level",
                    "field_label": "ICP Level",
                    "field_type": "picklist",
                    "options": "High,Medium,Low",
                    "field_config": {"ai_fill": bad_ai_fill},
                },
                _ACTOR,
                reason=None,
            )

    assert exc.value.status_code == 400
    assert "ai_fill" in exc.value.detail


# ---------------------------------------------------------------------------
# Test 3: non-AI field → ai_meta column NOT added
# ---------------------------------------------------------------------------

def test_non_ai_field_no_ai_meta_column():
    db = _make_db()
    provision_field(
        db,
        _MODULE,
        {
            "field_name": "notes",
            "field_label": "Notes",
            "field_type": "text",
        },
        _ACTOR,
        reason=None,
    )
    sql_all = " ".join(_executed_sql(db))
    assert "ai_meta" not in sql_all
