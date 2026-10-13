"""Bridge business-write event hook — plan §6.4.

One function, :func:`emit_business_write`, that every CRM-writing code
path is expected to call after a successful INSERT / UPDATE. Centralises
the two-row-per-write audit discipline:

1. ``changelog`` — one row per changed field (old/new values, typed).
2. ``interactions`` — one ``kind='action'`` row with pointer-level
   content (``{op, fields: [...]}``), keyed by ``subjects`` so the
   Phase 2 timeline UI can GIN-index-lookup by record.

The function was extracted from :mod:`hermes.crm.save` so that
non-REST writers (the feishu bot, the Phase 2 Agent writer, the
migration importer) share exactly the emit semantics of the REST layer.
Behaviour is byte-identical to the inline code it replaces; the test
suites in ``test_interactions.py`` continue to pass verbatim.

Phase 2+ expansions hinted at in the plan (RL sample generation on
``source='agent:*'``, ``rl_outcome_events`` on milestone field
transitions) attach here. They're not implemented yet — the consumers
don't exist — but the hook is the right seam to add them when they do,
rather than sprinkling ``if source == ...`` branches across Save /
Feishu / Agent.

Contract:

* Never raises on interaction-emit failure — primary write availability
  wins over event-stream completeness. A malformed emit just gets
  logged. Changelog failures DO propagate because the changelog is
  inside the same request-scope transaction as the INSERT/UPDATE, so
  silently swallowing would corrupt the audit trail.
* ``old`` may be an empty dict for inserts. ``new`` must at minimum
  include the payload that was written (not the full row — bookkeeping
  columns like ``updated_at`` are filtered internally).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from hermes.crm.db import DbWrapper
from hermes.crm.models.changelog_db import ChangelogDb
from hermes_cloud.app.interactions import emit as emit_interaction


logger = logging.getLogger(__name__)


# Columns the framework sets on every write — never part of a
# "user-meaningful changed fields" summary.
_BOOKKEEPING_COLS = frozenset(
    {"created_by", "updated_by", "created_at", "updated_at"}
)


def _should_dispatch_ai_fill(source: str) -> bool:
    """防自激环:AI 自己的写、以及工作流写,都不再触发 AI 填值(防 ai→workflow→ai 级联)。"""
    s = str(source or "")
    # workflow / workflow_undo 都算工作流写,不触发 AI 填值(防 ai→workflow→ai 级联)。
    return not s.startswith("agent:ai-attr") and not s.startswith("workflow")


def _values_equal(a: Any, b: Any) -> bool:
    """String-coerce compare that treats numeric types as equal across
    int/float/Decimal boundaries — matches kacrm's loose-compare habit.
    Lifted from :class:`Save._values_equal` unchanged."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        if isinstance(a, (int, float)) or isinstance(b, (int, float)):
            return float(a) == float(b)
    except (TypeError, ValueError):
        pass
    return str(a) == str(b)


def emit_business_write(
    *,
    db: DbWrapper,
    module_id: int,
    module_name: str,
    record_id: int,
    op: str,
    old: Dict[str, Any],
    new: Dict[str, Any],
    user_id: int,
    source: str = "manual",
    field_catalog: Optional[Iterable[Dict[str, Any]]] = None,
) -> None:
    """Write changelog + interaction rows for a CRM record write.

    Parameters
    ----------
    db:
        Request-scope :class:`DbWrapper`. Must be the SAME wrapper the
        primary INSERT/UPDATE used so the changelog lands in the same
        transaction.
    module_id, module_name:
        Collection the record belongs to. ``module_name`` goes into
        ``interactions.subjects[].module``.
    record_id:
        Primary key of the record just written.
    op:
        ``"create"`` / ``"update"`` / ``"delete"`` — appears as
        ``content.op`` in the interactions row.
    old:
        Row state before the write — ``{}`` for inserts. Used to diff
        against ``new`` so "update" emits narrow to genuinely-changed
        fields (no-op updates produce no interaction).
    new:
        Payload that was written. Bookkeeping columns are filtered out
        before ``content.fields`` is computed.
    user_id:
        Actor id — written to ``interactions.actors[].id`` and
        ``changelog.created_by``.
    source:
        Provenance stamp — written verbatim to ``interactions.source``.
        See :mod:`hermes_cloud.app.bridge.context` for the allowlist.
    field_catalog:
        Optional pre-fetched ``module_fields`` rows so callers that
        already loaded them (``Save`` does) don't force a redundant
        round-trip. When ``None`` the changelog uses best-effort
        "text" typing for fields it can't look up.
    """
    if not module_name:
        logger.warning("emit_business_write_missing_module record_id=%s", record_id)
        return

    # -- 1. changelog per field (inside the same txn as the main write) ---
    fields_by_name: Dict[str, Dict[str, Any]] = {}
    if field_catalog:
        for f in field_catalog:
            nm = str(f.get("field_name") or "")
            if nm:
                fields_by_name[nm] = f

    changelog = ChangelogDb(db)
    for fname, new_val in new.items():
        if fname in _BOOKKEEPING_COLS:
            continue
        old_val = old.get(fname) if isinstance(old, dict) else None
        if _values_equal(old_val, new_val):
            continue
        f = fields_by_name.get(fname) or {}
        changelog.log_field_change(
            module_id,
            record_id,
            fname,
            str(f.get("field_label") or fname),
            str(f.get("field_type") or "text"),
            old_val,
            new_val,
            user_id,
        )

    # -- 2. interactions summary (never fatal) ---------------------------
    changed_fields: List[str] = [
        k for k in new.keys() if k not in _BOOKKEEPING_COLS
    ]
    if op == "update" and old:
        changed_fields = [
            k
            for k in changed_fields
            if not _values_equal(old.get(k), new.get(k))
        ]
        if not changed_fields:
            return  # no-op update — don't emit noise

    # Embed a compact old→new preview so the timeline UI can show
    # "industry: tech → saas" without a second fetch. Values are coerced to
    # short strings — avoid dumping big JSONB blobs into every action row.
    def _preview(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (bool, int, float)):
            return v
        s = str(v)
        return s if len(s) <= 120 else s[:117] + "…"

    changes: Dict[str, Dict[str, Any]] = {}
    if op == "update" and isinstance(old, dict):
        for k in changed_fields:
            changes[k] = {"from": _preview(old.get(k)), "to": _preview(new.get(k))}
    elif op == "create":
        for k in changed_fields:
            v = new.get(k)
            if v not in (None, "", 0):
                changes[k] = {"to": _preview(v)}

    content: Dict[str, Any] = (
        {"op": op}
        if op == "delete"
        else {"op": op, "fields": changed_fields, "changes": changes} if changes
        else {"op": op, "fields": changed_fields}
    )
    # P1 事件溯源底座：interaction 发射与主写入同事务原子——失败必须
    # 传播，让 Save 的 db.transaction() 回滚整笔写入。不再吞噬。
    emit_interaction(
        kind="action",
        subjects=[{"module": module_name, "id": int(record_id)}],
        actors=[{"type": "user", "id": int(user_id)}] if user_id else None,
        content=content,
        source=source,
        db=db,
    )

    # Associate any files uploaded earlier in this conversation to the record
    # just written. We run inside the hermes process, so HERMES_SESSION_ID holds
    # the conversation id (set by the api_server chat-completions handler). The
    # cloud /api/chat/attachments endpoint stamped uploads with that same id.
    #
    # CRITICAL: this MUST use its own db_session, NOT the caller's ``db``. A
    # failure here (e.g. migration 0102 not yet applied → attachment_links
    # missing) would otherwise abort the caller's transaction and break the
    # business write itself — try/except cannot un-poison an aborted psycopg
    # txn. The link only needs ``conversation_id`` + the already-known
    # ``record_id`` (a plain int, no FK to the uncommitted row), so an
    # independent session is correct and fully isolated. Best-effort.
    if op in ("create", "update"):
        try:
            from gateway.session_context import get_session_env
            conv = get_session_env("HERMES_SESSION_ID", "")
            if conv:
                from hermes.crm.db import db_session
                from hermes_cloud.app.attachments.repo import (
                    link_conversation_attachments,
                )
                with db_session() as link_db:
                    link_conversation_attachments(
                        link_db,
                        conversation_id=conv,
                        target_module=module_name,
                        target_record_id=int(record_id),
                    )
        except Exception:  # noqa: BLE001 — linking must never break the write
            logger.debug("attachment_link_skipped", exc_info=True)

    # 工作流 event 触发(best-effort,绝不影响主写路径)。命中的工作流经
    # db.after_commit 在本写事务提交后才入 celery —— 保证 worker(独立连接)
    # 能读到已落库的新记录;不用 countdown(redis ETA 在本环境不可靠)。
    # source='workflow'/'workflow_undo' 的写不再级联触发工作流(防自激环 + 撤销不反弹)。
    if op in ("create", "update") and not str(source or "").startswith("workflow"):
        try:
            from hermes_cloud.app.crm.workflows import (
                _cascade_depth, match_event_workflows,
            )
            wf_ids = match_event_workflows(db, module_name, op)
            if wf_ids:
                rid = int(record_id)
                trig = f"event:{op}"
                # 级联深度:若本写由某工作流动作引发(同进程内 run_workflow 已把
                # contextvar 置为当前深度),下游 +1;用户直发写则 contextvar 为 0 →
                # 下游得深度 1。注意:event 派发与 worker 执行跨进程,真正的级联只在
                # 同进程链(如 utility-skill 回写)才能透传;跨 worker 链由接收侧的
                # cascade_depth>MAX_CASCADE 闸兜底(深度从该进程视角重新计)。
                depth = int(_cascade_depth.get() or 0) + 1

                cf = list(changed_fields)  # 串给 run_workflow 供 ``changed`` 条件判断
                import uuid
                # 本次写的触发实例键:重复投递同 key 挡住,不同更新各自 key 放行。
                evt_key = f"evt:{uuid.uuid4().hex}"
                def _enqueue_workflows() -> None:
                    from hermes_cloud.app.celery_app import run_workflow_task
                    for wid in wf_ids:
                        run_workflow_task.apply_async(
                            args=[wid, rid, trig],
                            kwargs={"cascade_depth": depth, "changed_fields": cf,
                                    "run_key": evt_key})

                db.after_commit(_enqueue_workflows)
        except Exception:  # noqa: BLE001 — 工作流派发失败绝不阻断业务写
            logger.debug("workflow_event_dispatch_skipped", exc_info=True)

    # AI 字段填值触发(best-effort,after_commit,绝不阻断主写)。
    if op in ("create", "update") and _should_dispatch_ai_fill(source):
        try:
            from hermes_cloud.app.crm.ai_attributes import ai_fields_for_module
            targets = ai_fields_for_module(db, module_name)
            if targets:
                if op == "create":
                    fire = [fn for fn, af in targets if (af.get("refresh") or {}).get("on_create")]
                else:
                    cf = set(changed_fields)
                    fire = [
                        fn for fn, af in targets
                        if (af.get("refresh") or {}).get("on_change")
                        and (
                            not ((af.get("inputs") or {}).get("fields"))
                            or cf & set((af.get("inputs") or {}).get("fields") or [])
                        )
                    ]
                if fire:
                    rid = int(record_id)

                    def _enqueue_ai() -> None:
                        from hermes_cloud.app.celery_app import ai_fill_task
                        ai_fill_task.apply_async(
                            args=[module_name, rid, fire,
                                  "on_create" if op == "create" else "on_change"],
                            countdown=5)
                    db.after_commit(_enqueue_ai)
        except Exception:  # noqa: BLE001
            logger.debug("ai_fill_dispatch_skipped", exc_info=True)

    # Charter v2 Phase 3 (2026-05-06): RL field changelog + outcome
    # event triggers removed along with the rl/ subsystem.


__all__ = ["emit_business_write"]
