"""Shared helpers for IM-style touchpoints.

Feishu / WeCom / WeChat / DingTalk / Slack / Telegram / Discord all
follow the same shape: a sender (external to the CRM) sends a text
message to a channel bound to a CRM user. Each adapter differs only
in the payload vocabulary.

This module centralises the "resolve sender → CRM subject" heuristic
so we don't hand-roll it per channel.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes.crm.db import DbWrapper, db_session

logger = logging.getLogger(__name__)


def resolve_subjects_by_identifier(
    *,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    external_id: Optional[str] = None,
    channel: Optional[str] = None,
    db: Optional[DbWrapper] = None,
) -> List[Dict[str, Any]]:
    """Best-effort CRM-record lookup from an external identifier.

    Resolution order (most authoritative first):

    1. **external_id + channel** — channel_bindings 精确映射，最权威。
       飞书 open_id 等渠道 ID 是一对一绑定，不存在歧义。
    2. **email** — 跨 contacts / accounts / leads 顺查。
    3. **phone** — 同上，仅在 email 无命中时触发。

    Returns ``[]`` if nothing matched; the interaction still lands in
    the Log, just un-linked to a record.

    The lookup is intentionally conservative: one shot per identifier,
    no fuzzy matching. Better to miss than to mis-link — charter
    定律 6 ("隐私、审计、可撤销是架构原语") treats false positives as
    privacy leaks.
    """
    def _run(db: DbWrapper) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []

        def _try(module: str, column: str, value: str) -> None:
            try:
                row = db.fetchone(
                    f"SELECT {module}_id AS id FROM {module} "
                    f"WHERE {column} = %s "
                    f"AND (deleted = FALSE OR deleted IS NULL) "
                    f"LIMIT 1",
                    (value,),
                )
                if row:
                    hits.append({"module": module, "id": int(row["id"])})
            except Exception:
                # Table/column may not exist yet depending on migration
                # state. Swallow — resolution is best-effort.
                logger.debug("resolve_subjects: %s.%s lookup failed", module, column)

        # 1. external_id + channel → channel_bindings（最权威，精确映射）
        if external_id and channel:
            from hermes_cloud.app.crm import channel_bindings_db
            try:
                row = channel_bindings_db.get(db, channel, external_id)
                if row:
                    hits.append({"module": row["module"], "id": int(row["record_id"])})
            except Exception:
                logger.debug(
                    "resolve_subjects: channel_bindings lookup failed "
                    "(channel=%s, external_id=%s)", channel, external_id,
                )

        # 2. email
        if email and not hits:
            _try("contacts", "email", email)
            if not hits:
                _try("accounts", "email", email)
            if not hits:
                _try("leads", "email", email)

        # 3. phone（fallback）
        if phone and not hits:
            _try("contacts", "phone", phone)
            if not hits:
                _try("accounts", "phone", phone)
            if not hits:
                _try("leads", "phone", phone)

        return hits

    if db is not None:
        return _run(db)
    with db_session() as owned:
        return _run(owned)


__all__ = ["resolve_subjects_by_identifier"]
