"""audit_log retention — Phase 5.5 (plan v1.2 §4.6 Day 4).

Monthly cron-driven retention sweep:
  · 90-day window kept hot in PG
  · rows older than 90 days dump to S3-like object storage as ndjson
    (one file per month, structured ``audit_log/YYYY-MM.ndjson``)
  · then DELETE archived rows

For hermes-cloud's local-first deployment (no S3 by default), the
archive step writes to ``data/audit_archive/`` on the local filesystem.
A real S3 client can be plugged in via the ``archive_writer`` arg.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from hermes.crm.db import DbWrapper

logger = logging.getLogger(__name__)


# Default retention window — 90 days hot in PG.
RETENTION_DAYS = 90

# Default archive root (local fs). Override per deployment.
DEFAULT_ARCHIVE_ROOT = Path("data/audit_archive")


def _serialize_row(row: dict) -> str:
    """JSONL one row. Datetimes → ISO. Skip ``None`` only if explicitly
    stripped to keep replay deterministic."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return json.dumps(out, ensure_ascii=False, default=str)


def _local_archive_writer(
    archive_root: Path = DEFAULT_ARCHIVE_ROOT,
) -> Callable[[str, Iterable[dict]], int]:
    """Default file-system archive writer. Production deployments
    should pass an S3-backed writer with the same signature."""
    def _write(month_label: str, rows: Iterable[dict]) -> int:
        archive_root.mkdir(parents=True, exist_ok=True)
        path = archive_root / f"{month_label}.ndjson"
        n = 0
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(_serialize_row(row) + "\n")
                n += 1
        return n
    return _write


def cleanup_audit_log(
    db: DbWrapper, *,
    retention_days: int = RETENTION_DAYS,
    archive_writer: Optional[Callable[[str, Iterable[dict]], int]] = None,
    dry_run: bool = False,
) -> dict:
    """Run one retention sweep.

    Returns a summary dict ``{"archived": int, "deleted": int}``.

    ``dry_run=True`` archives but does NOT delete (for first-month
    safety verification). After the first 2-3 months pass cleanly,
    flip to dry_run=False in cron.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
    writer = archive_writer or _local_archive_writer()

    try:
        old_rows = db.fetchall(
            """
            SELECT id, caller_id, scope_kind, resource_kind,
                   resource_filter, occurred_at
              FROM audit_log
             WHERE occurred_at < %s
             ORDER BY occurred_at
            """,
            (cutoff,),
        ) or []
    except Exception:
        logger.exception("audit_log retention scan failed")
        return {"archived": 0, "deleted": 0, "error": "scan_failed"}

    if not old_rows:
        return {"archived": 0, "deleted": 0}

    # Group rows by year-month for file partitioning
    by_month: dict[str, list] = {}
    for r in old_rows:
        ts = r.get("occurred_at")
        if isinstance(ts, datetime):
            label = ts.strftime("%Y-%m")
        else:
            label = "unknown"
        by_month.setdefault(label, []).append(r)

    archived_total = 0
    for label, rows in by_month.items():
        try:
            n = writer(label, rows)
            archived_total += n
        except Exception:
            logger.exception(
                "audit_log archive write failed month=%s n=%s",
                label, len(rows),
            )
            return {
                "archived": archived_total, "deleted": 0,
                "error": f"archive_write_failed:{label}",
            }

    deleted = 0
    if not dry_run:
        try:
            ids = [r["id"] for r in old_rows]
            db.execute(
                "DELETE FROM audit_log WHERE id = ANY(%s::bigint[])",
                (ids,),
            )
            deleted = len(ids)
            db.commit()
        except Exception:
            logger.exception("audit_log retention delete failed")
            try:
                db.rollback()
            except Exception:
                pass
            return {
                "archived": archived_total, "deleted": 0,
                "error": "delete_failed",
            }

    return {"archived": archived_total, "deleted": deleted}


__all__ = ["cleanup_audit_log", "RETENTION_DAYS"]
