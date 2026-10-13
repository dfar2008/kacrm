"""Backfill module_fields label i18n — zh-CN primary label + en-US translation.

Historically the seed wrote English into ``module_fields.field_label``
("Account Name", "Owner", ...) and the CRM read paths compensated with a
hardcoded English→中文 dictionary (``localize_field_label``). The schema
field-builder bypassed that and surfaced raw English, and the per-field
``field_config.label_i18n`` translation slot was never populated.

This migration makes ``field_label`` the single primary (zh-CN) label and
moves the English into ``field_config.label_i18n['en-US']`` — so every
reader (builder / detail / list / timeline) agrees, and the 标签翻译
panel becomes the live source of the English translation.

The zh-CN / en-US labels are derived by running the existing
``localize_field_label`` resolver against each row, so the result matches
exactly what record views already display today.

Idempotent: a row whose ``field_label`` already equals its zh-CN form
(e.g. agent/admin-added fields that were always Chinese) is left
untouched. On a fresh DB ``module_fields`` is still empty at migration
time, so this is a no-op there — the corrected seed handles new installs.

Revision ID: 0068
Revises: 0067
Create Date: 2026-05-21
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def _coerce(cfg_raw) -> dict:
    if isinstance(cfg_raw, dict):
        return dict(cfg_raw)
    if isinstance(cfg_raw, str) and cfg_raw.strip():
        try:
            parsed = json.loads(cfg_raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def upgrade() -> None:
    from hermes.crm.i18n.field_labels import localize_field_label

    conn = op.get_bind()
    # NB: join ``collections`` directly, not the ``modules`` compat view —
    # that view isn't created until 0083, so a fresh-DB replay would hit
    # ``relation "modules" does not exist`` here. ``collections.name`` is
    # what the view exposes as ``table_name`` (see 0083).
    rows = conn.execute(sa.text(
        "SELECT mf.id, mf.field_name, mf.field_label, mf.field_config, "
        "       c.name AS table_name "
        "FROM module_fields mf JOIN collections c ON c.id = mf.module_id"
    )).mappings().all()

    for r in rows:
        field_name = (r["field_name"] or "").strip()
        raw_label = (r["field_label"] or "").strip()
        table_name = (r["table_name"] or "").strip()
        field_id = int(r["id"] or 0) or None

        zh = localize_field_label(
            table_name=table_name, field_name=field_name,
            field_label=raw_label, field_id=field_id, locale="zh-CN",
        ).strip()
        en = localize_field_label(
            table_name=table_name, field_name=field_name,
            field_label=raw_label, field_id=field_id, locale="en-US",
        ).strip()

        # Already zh-CN primary (or no translation known) → leave it.
        if not zh or zh == raw_label:
            continue

        cfg = _coerce(r["field_config"])
        label_i18n = cfg.get("label_i18n")
        if not isinstance(label_i18n, dict):
            label_i18n = {}
        # Preserve any explicit en-US the admin already set.
        if en and en != zh and not label_i18n.get("en-US"):
            label_i18n["en-US"] = en
        if label_i18n:
            cfg["label_i18n"] = label_i18n

        conn.execute(
            sa.text(
                "UPDATE module_fields "
                "SET field_label = :lbl, field_config = CAST(:cfg AS json) "
                "WHERE id = :id"
            ),
            {
                "lbl": zh,
                "cfg": json.dumps(cfg, ensure_ascii=False) if cfg else None,
                "id": r["id"],
            },
        )


def downgrade() -> None:
    """Best-effort reverse: restore the English field_label from
    ``label_i18n['en-US']`` where present."""
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, field_label, field_config FROM module_fields"
    )).mappings().all()
    for r in rows:
        cfg = _coerce(r["field_config"])
        en = (cfg.get("label_i18n") or {}).get("en-US") if isinstance(
            cfg.get("label_i18n"), dict) else None
        if not en:
            continue
        conn.execute(
            sa.text("UPDATE module_fields SET field_label = :lbl WHERE id = :id"),
            {"lbl": str(en), "id": r["id"]},
        )
