"""Backfill module_fields.field_config.option_labels — picklist labels.

Seeded picklists stored ``options`` as a bare CSV of values
("new,contacted,qualified,lost"); the seed had {value,label} dicts but
``_field`` dropped the labels. So a select value rendered as its raw
storage key ("new", "feishu") everywhere.

The ``options`` column format is a hard contract — Save.validate,
edit_query and the add_field_option route all parse it (CSV or JSON).
So the labels do NOT go into ``options``; they go into
``field_config.option_labels`` — {value: {locale: label}} — alongside
the field's own ``label_i18n``. ``localize_picklist_options`` reads
them back.

Labels are sourced from the seed spec (MODULE_FIELDS_SPEC); for
admin-managed JSON picklists any inline option ``text`` is captured as
the zh-CN label. The ``options`` column is never modified, and an
option value already present in ``option_labels`` is left untouched.

Idempotent. On a fresh DB module_fields is empty here — the corrected
seed handles new installs.

Revision ID: 0069
Revises: 0068
Create Date: 2026-05-21
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

_PICKLIST_TYPES = ("select", "multiSelect", "picklist", "radio", "checkbox")


def _coerce(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def upgrade() -> None:
    from hermes.crm.seed_metadata import MODULE_FIELDS_SPEC
    from hermes.crm.i18n.options import parse_picklist_options

    # seed lookup: (module_name, field_name) -> {value: {locale: label}}
    seed_labels: dict = {}
    for mod_name, specs in MODULE_FIELDS_SPEC.items():
        for spec in specs:
            ol = (spec.get("field_config") or {}).get("option_labels")
            if isinstance(ol, dict) and ol:
                seed_labels[(mod_name, spec["field_name"])] = ol

    conn = op.get_bind()
    # NB: join ``collections`` directly, not the ``modules`` compat view —
    # that view isn't created until 0083, so a fresh-DB replay would hit
    # ``relation "modules" does not exist`` here. ``collections.name`` is
    # the view's ``name`` column (see 0083).
    rows = conn.execute(
        sa.text(
            "SELECT mf.id, mf.field_name, mf.options, mf.field_config, "
            "       c.name AS module_name "
            "FROM module_fields mf JOIN collections c ON c.id = mf.module_id "
            "WHERE mf.field_type = ANY(:types) "
            "AND mf.options IS NOT NULL AND mf.options <> ''"
        ),
        {"types": list(_PICKLIST_TYPES)},
    ).mappings().all()

    for r in rows:
        opts = parse_picklist_options(r["options"])
        if not opts:
            continue
        cfg = _coerce(r["field_config"])
        option_labels = cfg.get("option_labels")
        option_labels = dict(option_labels) if isinstance(option_labels, dict) else {}
        seed_ol = seed_labels.get((r["module_name"], r["field_name"]), {})

        changed = False
        for opt in opts:
            value = opt["value"]
            if value in option_labels:
                continue  # keep an already-recorded label
            label: dict | None = None
            if value in seed_ol:
                label = dict(seed_ol[value])
            else:
                # admin-managed JSON picklist: capture its inline text.
                text = str(opt.get("text") or "").strip()
                if text and text != value:
                    label = {"zh-CN": text}
                    inline_i18n = opt.get("label_i18n")
                    if isinstance(inline_i18n, dict):
                        for loc, val in inline_i18n.items():
                            if val:
                                label[str(loc)] = str(val)
            if label:
                option_labels[value] = label
                changed = True

        if not changed:
            continue
        cfg["option_labels"] = option_labels
        conn.execute(
            sa.text(
                "UPDATE module_fields SET field_config = CAST(:c AS json) "
                "WHERE id = :id"
            ),
            {"c": json.dumps(cfg, ensure_ascii=False), "id": r["id"]},
        )


def downgrade() -> None:
    """Drop the option_labels key from field_config (leaves options as-is)."""
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, field_config FROM module_fields "
        "WHERE field_config IS NOT NULL"
    )).mappings().all()
    for r in rows:
        cfg = _coerce(r["field_config"])
        if "option_labels" not in cfg:
            continue
        cfg.pop("option_labels", None)
        conn.execute(
            sa.text(
                "UPDATE module_fields SET field_config = CAST(:c AS json) "
                "WHERE id = :id"
            ),
            {"c": json.dumps(cfg, ensure_ascii=False) if cfg else None, "id": r["id"]},
        )
