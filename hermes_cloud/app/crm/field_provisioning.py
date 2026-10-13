"""Field provisioning — the one correct way to add a CRM field.

A CRM field has two halves:

  1. metadata — a row in ``module_fields`` (label, type, sort, etc.)
  2. a physical column on the ``<module>`` data table.

Historically two routes added fields and only one (the field_proposals
approval route) created the physical column. The direct route
``POST /crm/modules/{m}/fields`` only inserted metadata, so any field
it created would 500 the moment a SELECT/SUM touched the missing
column. ``provision_field`` is the shared, correct implementation;
both routes now call it.

This module also owns the canonical ``field_type -> SQL column type``
map. Adding a new field type means adding one entry here.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from hermes.crm.autonumber import format_number
from hermes.crm.db import DbWrapper
from hermes.crm.formula import FormulaError, evaluate
from hermes.crm.models.module_fields_db import ModuleFieldsDb
from hermes.crm.models.schema_changelog_db import SchemaChangelogDb


# field_type (lowercased) -> SQL column type for ALTER TABLE ADD COLUMN.
# See docs/superpowers/specs/2026-05-20-schema-field-builder-design.md §5.3.
SQL_TYPE_MAP: dict[str, str] = {
    "text":        "TEXT",
    "textarea":    "TEXT",
    "email":       "TEXT",
    "phone":       "TEXT",
    "url":         "TEXT",
    "richtext":    "TEXT",
    "file":        "TEXT",
    "image":       "TEXT",
    "multiselect": "TEXT",
    "picklist":    "VARCHAR(250)",
    "number":      "NUMERIC",
    "currency":    "NUMERIC",
    "formula":     "NUMERIC",
    "date":        "DATE",
    "datetime":    "TIMESTAMP WITH TIME ZONE",
    "boolean":     "BOOLEAN",
    "user":        "INTEGER",
    "department":  "INTEGER",
    "relation":    "INTEGER",
    "autonumber":  "VARCHAR(64)",
}


# Legacy field_type 别名 → canonical。select/radio 是 picklist 的旧名,
# multipicklist 是 multiselect 的旧名;归一到一种,避免命名分叉再生。
FIELD_TYPE_ALIASES: dict[str, str] = {
    "select":        "picklist",
    "radio":         "picklist",
    "multipicklist": "multiselect",
}


def canonical_field_type(field_type: str) -> str:
    """把 legacy 别名归一到 canonical field_type(不区分大小写)。"""
    if not field_type:
        return field_type
    return FIELD_TYPE_ALIASES.get(field_type.strip().lower(), field_type)


def resolve_sql_type(field_type: str) -> Optional[str]:
    """Map a field_type to its SQL column type.

    Case-insensitive (``autoNumber`` is stored mixed-case in
    metadata). Returns ``None`` for unknown types — callers turn that
    into an HTTP 400.
    """
    if not field_type:
        return None
    return SQL_TYPE_MAP.get(field_type.strip().lower())


# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[a-z][a-z0-9_]*")


def _validate_identifier(name: str) -> None:
    """Reject anything that isn't clean snake_case.

    field_name lands directly in dynamic SQL (ALTER TABLE, list/edit
    queries) so it must be a safe identifier. Raises HTTP 400.
    """
    if not name or not _IDENT_RE.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail=f"invalid field_name {name!r}: must be [a-z][a-z0-9_]*",
        )


# ---------------------------------------------------------------------------
# Row backfill — extension point for P2 (autoNumber) / P3 (formula)
# ---------------------------------------------------------------------------

def _coerce_field_config(raw) -> dict:
    """``field_config`` may be a dict (psycopg JSON adapter) or a JSON
    string (raw row read). Return a dict; tolerate None / bad JSON."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _write_formula_over_rows(db, module, field_meta) -> None:
    """Evaluate one formula field for every row of a module and write
    the ``NUMERIC`` column.

    Shared by the formula branch of :func:`_backfill_new_field` (new
    field) and :func:`recompute_formula_field` (expression changed).
    A row whose expression fails to evaluate is skipped — one broken
    row never aborts the batch.
    """
    table = module["name"]
    field_name = field_meta.get("field_name") or ""
    pk = f"{table}_id"
    expr = (
        _coerce_field_config(field_meta.get("field_config")).get("formula")
        or {}
    ).get("expression")
    if not expr or not str(expr).strip():
        return
    rows = db.fetchall(f'SELECT * FROM "{table}"')
    for row in rows or []:
        row_dict = dict(row)
        row_id = row_dict.get(pk)
        if row_id is None:
            continue
        try:
            result = evaluate(str(expr), row_dict)
        except FormulaError:
            continue  # broken formula for this row — skip
        db.execute(
            f'UPDATE "{table}" SET "{field_name}" = %s WHERE {pk} = %s',
            (result, row_id),
        )


def recompute_formula_field(db, module: dict, field_meta: dict) -> None:
    """Recompute a formula field's ``NUMERIC`` column across every row
    of a module.

    Called by the PATCH route ``PATCH /crm/modules/{m}/fields/
    {field_name}`` (Phase P1) after ``field_config.formula.expression``
    changes — the stored values must be refreshed against the new
    expression. Batched over all module rows; safe to call on a large
    table (one UPDATE per row inside the caller's transaction).
    """
    _write_formula_over_rows(db, module, field_meta)


def _backfill_new_field(
    db: DbWrapper, module: dict, field_meta: dict
) -> None:
    """Backfill existing rows for a newly provisioned field.

    Called by ``provision_field`` after the column (and, for
    autoNumber, the SEQUENCE) has been created — same transaction.

    P2: autoNumber. P3: formula. Other field types: no-op (NULL is the
    correct default).

    ``field_meta`` carries: field_name, field_type, field_config.
    """
    field_type = (field_meta.get("field_type") or "").strip()
    table = module["name"]
    field_name = field_meta.get("field_name") or ""

    if field_type == "autoNumber":
        config = _coerce_field_config(field_meta.get("field_config")).get(
            "autonumber"
        ) or {}
        seq_name = f"autonum_{table}_{field_name}"
        pk = f"{table}_id"
        rows = db.fetchall(
            f'SELECT {pk} AS id FROM "{table}" ORDER BY {pk} ASC'
        )
        now = datetime.now(timezone.utc)
        for row in rows or []:
            seq_row = db.fetchone(f"SELECT nextval('{seq_name}') AS nextval")
            seq_value = int(seq_row["nextval"])
            formatted = format_number(config, seq_value, now)
            db.execute(
                f'UPDATE "{table}" SET "{field_name}" = %s WHERE {pk} = %s',
                (formatted, row["id"]),
            )
        return

    if field_type == "formula":
        _write_formula_over_rows(db, module, field_meta)
        return

    return  # all other types: NULL is correct


# ---------------------------------------------------------------------------
# provision_field — the one correct way to add a field
# ---------------------------------------------------------------------------

def provision_field(
    db: DbWrapper,
    module: dict,
    field_data: dict,
    actor: dict,
    reason: Optional[str],
) -> dict:
    """Create a CRM field: metadata row + physical column, atomically.

    ``module`` is a resolved module row dict (keys include ``id``,
    ``name``, ``table_name``).

    ``field_data`` keys (all but the first three optional):
      field_name, field_label, field_type, is_required, is_searchable,
      is_readonly, options, default_value, description, field_config,
      sort_order.

    Runs entirely inside one ``db.transaction()``:
      1. validate field_name + resolve SQL type (400 on failure)
      2. ModuleFieldsDb.insert_field — metadata row
      3. ALTER TABLE ADD COLUMN IF NOT EXISTS — physical column
      4. autoNumber: CREATE SEQUENCE IF NOT EXISTS (pure DDL)
      5. _backfill_new_field — P0 no-op; P2/P3 flesh out
      6. SchemaChangelogDb.insert_entry(op="add_field")

    Returns ``{"field_id": int, "field_name": str}``.
    """
    name = str(field_data.get("field_name") or "").strip()
    label = str(field_data.get("field_label") or "").strip()
    # 归一 legacy 别名(select/radio→picklist, multipicklist→multiselect)在落库前,
    # 确保只有 canonical field_type 进表,命名分叉不再生。
    ftype = canonical_field_type(str(field_data.get("field_type") or "").strip())
    if not name or not label or not ftype:
        raise HTTPException(
            status_code=400,
            detail="field_name, field_label, field_type all required",
        )
    _validate_identifier(name)

    # ai_fill 校验（spec §5）。
    _raw_cfg = field_data.get("field_config") or {}
    _ai_fill = _raw_cfg.get("ai_fill") if isinstance(_raw_cfg, dict) else None
    if _ai_fill and _ai_fill.get("enabled"):
        from hermes_cloud.app.crm.ai_attributes import validate_ai_fill, AiFillError
        _existing = {
            str(f.get("field_name"))
            for f in (ModuleFieldsDb(db).get_module_fields(int(module["id"])) or [])
        }
        _existing.add(name)
        try:
            validate_ai_fill(ftype, _ai_fill, _existing)
        except AiFillError as e:
            raise HTTPException(status_code=400, detail=f"ai_fill: {e}")

    sql_type = resolve_sql_type(ftype)
    if not sql_type:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported field_type for column add: {ftype!r}",
        )

    table_name = str(module["table_name"])
    module_id = int(module["id"])
    module_name = str(module["name"])

    # field_config is a dict (or None); the JSON column wants JSON text.
    raw_config = field_data.get("field_config")
    config_json: Optional[str] = (
        json.dumps(raw_config) if raw_config is not None else None
    )

    # Normalize select-like options to the canonical CSV-of-values form, with
    # display labels lifted into ``field_config.option_labels``. The agent
    # often passes the rich JSON-object form (``[{value,text,label}]``);
    # storing that raw breaks the CSV-based option mutation endpoints
    # (add/delete/reorder split it on commas → corruption). parse_picklist_options
    # reads both forms; we emit values as CSV and put labels where
    # localize_picklist_options expects them (field_config.option_labels).
    options_value = field_data.get("options")
    if (
        ftype.lower() in {"picklist", "multiselect"}  # ftype 已在入口归一
        and options_value is not None
    ):
        try:
            from hermes.crm.i18n.options import parse_picklist_options
            from hermes.crm.picklist import join_options
            _dicts = parse_picklist_options(options_value)
            if _dicts:
                _values: list = []
                _labels: dict = {}
                for _o in _dicts:
                    _v = str(_o.get("value") or _o.get("text") or "").strip()
                    if not _v or "," in _v:
                        continue
                    if _v not in _values:
                        _values.append(_v)
                    _txt = str(_o.get("text") or "").strip()
                    _i18n = _o.get("label_i18n")
                    _entry = (
                        {str(k): str(v) for k, v in _i18n.items() if v}
                        if isinstance(_i18n, dict) else {}
                    )
                    if _txt and _txt != _v and not _entry:
                        _entry = {"zh-CN": _txt}
                    if _entry:
                        _labels[_v] = _entry
                options_value = join_options(_values)
                if _labels:
                    _cfg = {}
                    if config_json:
                        try:
                            _cfg = json.loads(config_json) or {}
                        except (ValueError, TypeError):
                            _cfg = {}
                    _ol = dict(_cfg.get("option_labels") or {})
                    for _k, _vv in _labels.items():
                        _ol.setdefault(_k, _vv)
                    _cfg["option_labels"] = _ol
                    config_json = json.dumps(_cfg)
        except Exception:
            # Never block field creation on option normalization.
            pass

    meta = {
        "module_id":     module_id,
        "field_name":    name,
        "field_label":   label,
        "field_type":    ftype,
        "is_required":   bool(field_data.get("is_required", False)),
        "is_searchable": bool(field_data.get("is_searchable", False)),
        "is_readonly":   bool(field_data.get("is_readonly", False)),
        "sort_order":    int(field_data.get("sort_order", 0)),
        "options":       options_value,
        "default_value": field_data.get("default_value"),
        "description":   field_data.get("description"),
        "field_config":  config_json,
    }

    with db.transaction():
        field_id = ModuleFieldsDb(db).insert_field(meta)

        # Physical column. IF NOT EXISTS keeps this idempotent —
        # critical because a failed provision_field must leave the
        # metadata row and the physical column consistent.
        db.execute(
            f'ALTER TABLE "{table_name}" '
            f'ADD COLUMN IF NOT EXISTS "{name}" {sql_type}'
        )

        # ai_fill 字段:惰性给本模块加 ai_meta JSONB（每记录每 AI 字段 provenance）。
        if _ai_fill and _ai_fill.get("enabled"):
            db.execute(
                f'ALTER TABLE "{table_name}" '
                f'ADD COLUMN IF NOT EXISTS ai_meta JSONB'
            )

        # autoNumber: one Postgres SEQUENCE per field is the counter.
        # Pure DDL — kept in P0. Row backfill (advancing the sequence
        # past existing records) is P2, done in _backfill_new_field.
        if ftype.lower() == "autonumber":
            seq_name = f"autonum_{module_name}_{name}"
            db.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq_name}")

        # Extension point — P0 no-op; P2/P3 backfill existing rows.
        _backfill_new_field(
            db, module,
            {"field_name": name, "field_type": ftype,
             "field_config": raw_config},
        )

        SchemaChangelogDb(db).insert_entry(
            actor_type="user",
            actor_id=int(actor["id"]),
            actor_label=actor.get("username"),
            module_id=module_id,
            module_name=module_name,
            field_id=field_id,
            field_name=name,
            op="add_field",
            before=None,
            after={k: v for k, v in meta.items() if k != "module_id"},
            reason=reason,
        )

    # ai_fill on_create:事务提交后异步回填存量行(不在事务内跑 LLM)。
    if _ai_fill and _ai_fill.get("enabled") and (_ai_fill.get("refresh") or {}).get("on_create"):
        try:
            from hermes_cloud.app.celery_app import ai_fill_backfill_task
            ai_fill_backfill_task.apply_async(args=[module_name, name])
        except Exception:
            logger.debug("ai_fill_backfill_dispatch_skipped", exc_info=True)

    return {"field_id": field_id, "field_name": name}
