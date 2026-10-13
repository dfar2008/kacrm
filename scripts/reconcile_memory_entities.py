#!/usr/bin/env python3
"""Reconcile legacy crm:<module>#<id> entities into canonical CRM entities.

Default mode is dry-run. Pass --apply to execute merges.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from hermes.crm.db import db_session
from hermes.memory.entities import merge_entities


@dataclass
class Stats:
    scanned: int = 0
    candidates: int = 0
    merged: int = 0
    skipped_same: int = 0
    skipped_missing_canonical: int = 0
    errors: int = 0

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "candidates": self.candidates,
            "merged": self.merged,
            "skipped_same": self.skipped_same,
            "skipped_missing_canonical": self.skipped_missing_canonical,
            "errors": self.errors,
        }


def _rows():
    sql = """
    SELECT e.id AS legacy_entity_id,
           e.entity_type,
           e.canonical,
           split_part(e.entity_type, ':', 2) AS module_name,
           split_part(e.canonical, '#', 2)::bigint AS record_id
    FROM memory_entities e
    WHERE e.entity_type LIKE 'crm:%'
      AND e.canonical LIKE '%#%'
    ORDER BY e.id
    """
    with db_session() as db:
        return db.fetchall(sql) or []


def _canonical_entity_id(module: str, record_id: int):
    table = str(module or "").strip().lower()
    if table not in {"accounts", "contacts", "leads", "potentials"}:
        return None
    pk = f"{table}_id"
    with db_session() as db:
        row = db.fetchone(
            f"SELECT memory_entity_id FROM {table} WHERE {pk}=%s AND deleted=FALSE",
            (int(record_id),),
        )
    if not row:
        return None
    eid = row.get("memory_entity_id")
    return int(eid) if eid else None


def reconcile(*, apply: bool) -> dict:
    stats = Stats()
    rows = _rows()
    for r in rows:
        stats.scanned += 1
        legacy_id = int(r["legacy_entity_id"])
        module = str(r.get("module_name") or "")
        try:
            record_id = int(r.get("record_id"))
        except Exception:
            stats.errors += 1
            continue
        canonical_id = _canonical_entity_id(module, record_id)
        if not canonical_id:
            stats.skipped_missing_canonical += 1
            continue
        if canonical_id == legacy_id:
            stats.skipped_same += 1
            continue
        stats.candidates += 1
        if not apply:
            continue
        try:
            with db_session() as db:
                merge_entities(src_id=legacy_id, dst_id=canonical_id, db=db)
            stats.merged += 1
        except Exception:
            stats.errors += 1
    return stats.to_dict()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="execute merges (default is dry-run)")
    args = p.parse_args()
    summary = reconcile(apply=bool(args.apply))
    summary["mode"] = "apply" if args.apply else "dry_run"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
