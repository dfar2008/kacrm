"""工作流引擎 —— agent 著作、按动作选执行器。

规则(workflows)与执行(run_workflow)分离:
- **确定性动作**(update_field / create_task / create_record)由引擎直接经
  ``Save`` 执行 —— 可靠、零 LLM 成本、可重放。
- **判断/沟通动作**(agent_task)丢给 per-profile agent 跑一轮:POST 到云的
  ``/internal/utility/run-skill``(loopback 免 key),由 web 进程的
  ``run_utility_skill`` 拉起 profile 子进程的 agent 执行。

触发:
- event —— ``emit_business_write`` 后置调 :func:`match_event_workflows` 拿到命中
  的工作流 id,celery 异步 :func:`run_workflow`(带 countdown 让写先提交)。
- schedule —— celery beat tick 调 :func:`due_schedule_workflows`。

``workflow_runs`` 落每次执行(审计 + 防重:同 工作流×触发记录×触发类型 的 done
只记一次)。
"""
from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from typing import Any, Dict, Iterable, List, Optional

from hermes.crm.db import DbWrapper, db_session

logger = logging.getLogger(__name__)

# ── 级联安全(S3 defense-in-depth)──────────────────────────────────
# 已有去重(同 工作流×记录×触发 done 只跑一次)挡同记录同触发自激;此处再加一道
# 跨工作流硬上限,挡 A→B→A 链式级联。每次 run_workflow 把入站深度写进 contextvar,
# 其动作引发的下游写在 after_commit 入队时读取并 +1。超过 MAX_CASCADE 直接 skip。
MAX_CASCADE = 5
_cascade_depth: ContextVar[int] = ContextVar("wf_cascade_depth", default=0)

# 连续失败自动停用阈值(S3):最近 K 次 run 全 failed 才停用,单次失败不动。
MAX_CONSEC_FAILURES = 5

# ── 条件求值 ────────────────────────────────────────────────────────


def _coerce_num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# op 别名:agent/客户端可能用友好名(gte/lt/eq…),统一映射到引擎符号。
# 不然 _cmp 不认 → 条件恒假 → 动作恒跳过(分支空转)。
_OP_ALIAS = {
    "eq": "=", "equals": "=", "ne": "!=", "neq": "!=", "not_equals": "!=",
    "gt": ">", "lt": "<", "gte": ">=", "lte": "<=", "ge": ">=", "le": "<=",
    "has": "contains", "is_present": "present", "is_absent": "absent",
}


def _norm_op(op: str) -> str:
    o = str(op or "").strip()
    return _OP_ALIAS.get(o.lower(), o)


def _cmp(left: Any, op: str, right: Any) -> bool:
    op = _norm_op(op)
    if op in ("present", "notnull"):
        return left is not None and str(left) != ""
    if op in ("absent", "isnull"):
        return left is None or str(left) == ""
    if op == "=":
        return str(left) == str(right)
    if op == "!=":
        return str(left) != str(right)
    if op == "contains":
        return right is not None and str(right) in str(left or "")
    # 数值比较 —— 两边能转 float 才比;否则 False(不误触发)
    ln, rn = _coerce_num(left), _coerce_num(right)
    if op in (">", "<", ">=", "<=") and ln is not None and rn is not None:
        return {
            ">": ln > rn, "<": ln < rn, ">=": ln >= rn, "<=": ln <= rn,
        }[op]
    return False


def evaluate_condition(
    record: Dict[str, Any],
    condition: Optional[Dict[str, Any]],
    changed_fields: Optional[Iterable[str]] = None,
) -> bool:
    """条件结构:``{"all": [{field, op, value}]}``(全 AND)。空 = 永真。

    特殊 op ``changed``:本字段在这次写里发生了变化才成立。需要 ``changed_fields``
    (event 触发由 emit_business_write 串入);schedule/无 diff 时 changed 恒不成立。
    """
    if not condition:
        return True
    clauses = condition.get("all") or []
    if not isinstance(clauses, list) or not clauses:
        return True
    cf = set(changed_fields) if changed_fields else None
    for c in clauses:
        if not isinstance(c, dict):
            return False
        field = str(c.get("field") or "")
        op = str(_norm_op(c.get("op") or "="))
        val = c.get("value")
        cur = record.get(field)
        if op == "changed":
            if not cf or field not in cf:
                return False
            continue
        if not _cmp(cur, op, val):
            return False
    return True


# ── 模板替换 ────────────────────────────────────────────────────────


def _render(template: Any, record: Dict[str, Any]) -> Any:
    """把字符串里的 ``{{field}}`` 用 record 的值替换;非字符串原样返回。"""
    if not isinstance(template, str) or "{{" not in template:
        return template
    out = template
    for k, v in record.items():
        token = "{{" + k + "}}"
        if token in out:
            out = out.replace(token, "" if v is None else str(v))
    return out


# ── 匹配(供 emit_business_write / beat 调)─────────────────────────


def match_event_workflows(
    db: DbWrapper, module_name: str, op: str,
) -> List[int]:
    """返回该 (module, op) 下启用的 event 工作流 id 列表。条件求值放到执行时
    (run_workflow 会用新记录复核),这里只做廉价的索引命中。"""
    try:
        rows = db.fetchall(
            """
            SELECT id FROM workflows
             WHERE trigger_type = 'event' AND enabled AND NOT deleted
               AND status = 'live'
               AND module = %s
               AND (trigger_on = %s OR trigger_on IS NULL)
            """,
            (module_name, op),
        )
        return [int(r["id"]) for r in (rows or [])]
    except Exception:
        # workflows 表不存在(迁移未跑)等 —— 绝不影响主写路径
        return []


def list_workflows(db: DbWrapper, module: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出工作流(可按模块过滤),供模块管理 UI 只读展示。返回含触发/条件/动作。"""
    try:
        sql = (
            "SELECT id, name, trigger_type, module, trigger_on, schedule_cron, "
            "condition, actions, enabled, status, created_at, updated_at "
            "FROM workflows WHERE NOT deleted"
        )
        params: tuple = ()
        if module:
            sql += " AND module = %s"
            params = (module,)
        sql += " ORDER BY module, id"
        rows = db.fetchall(sql, params)
        out: List[Dict[str, Any]] = []
        for r in (rows or []):
            d = dict(r)
            # condition/actions 可能是 jsonb(已 dict)或字符串,统一成对象。
            for k in ("condition", "actions"):
                v = d.get(k)
                if isinstance(v, str):
                    try:
                        d[k] = json.loads(v)
                    except Exception:
                        pass
            out.append(d)
        return out
    except Exception:
        return []


def list_workflow_runs(
    db: DbWrapper,
    module: str,
    workflow_id: Optional[int] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """列出某模块工作流的执行历史(只读,审计 UI 用)。可按 workflow_id 过滤。

    返回每次 run:触发记录、各动作结果(detail.results)、状态、时间。
    detail 可能是 jsonb(已 dict)或字符串,统一解析;空时回退 {}。
    """
    try:
        lim = max(1, min(int(limit or 100), 500))
        sql = (
            "SELECT r.id, r.workflow_id, w.name AS workflow_name, "
            "r.trigger_record_id, r.trigger, r.status, r.detail, r.created_at "
            "FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id "
            "WHERE w.module = %s"
        )
        params: tuple = (module,)
        if workflow_id is not None:
            sql += " AND r.workflow_id = %s"
            params = (module, int(workflow_id))
        sql += " ORDER BY r.created_at DESC LIMIT %s"
        params = params + (lim,)
        rows = db.fetchall(sql, params)
        out: List[Dict[str, Any]] = []
        for r in (rows or []):
            d = dict(r)
            detail = d.get("detail")
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail)
                except Exception:
                    detail = {}
            if not isinstance(detail, dict):
                detail = {}
            created = d.get("created_at")
            out.append({
                "id": d.get("id"),
                "workflow_id": d.get("workflow_id"),
                "workflow_name": d.get("workflow_name"),
                "trigger_record_id": d.get("trigger_record_id"),
                "trigger": d.get("trigger"),
                "status": d.get("status"),
                "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
                "results": detail.get("results") or [],
            })
        return out
    except Exception:
        return []


def set_workflow_enabled(db: DbWrapper, workflow_id: int, enabled: bool) -> bool:
    """启用/停用一条工作流。返回是否命中。"""
    row = db.fetchone(
        "UPDATE workflows SET enabled = %s, updated_at = now() "
        "WHERE id = %s AND NOT deleted RETURNING id",
        (bool(enabled), int(workflow_id)),
    )
    db.commit()
    return row is not None


def set_workflow_status(db: DbWrapper, workflow_id: int, status: str) -> bool:
    """切 draft/live 生命周期态。draft 不被 event/schedule 触发(仅 test-run 可跑)。
    返回是否命中;非法 status 抛 ValueError。"""
    s = str(status or "").strip().lower()
    if s not in ("draft", "live"):
        raise ValueError(f"invalid status {status!r} (want draft/live)")
    row = db.fetchone(
        "UPDATE workflows SET status = %s, updated_at = now() "
        "WHERE id = %s AND NOT deleted RETURNING id",
        (s, int(workflow_id)),
    )
    db.commit()
    return row is not None


def delete_workflow(db: DbWrapper, workflow_id: int) -> bool:
    """软删工作流(deleted=true)。列表/引擎已过滤 NOT deleted,删后即从列表消失、
    不再被 event/schedule 触发;可改回 deleted=false 恢复。返回是否命中。"""
    row = db.fetchone(
        "UPDATE workflows SET deleted = true, updated_at = now() "
        "WHERE id = %s AND NOT deleted RETURNING id",
        (int(workflow_id),),
    )
    db.commit()
    return row is not None


# 条件子句允许的算子(与引擎 _cmp/evaluate_condition 保持一致)。
ALLOWED_OPS = {
    "changed", "present", "notnull", "absent", "isnull",
    "=", "!=", "contains", ">", "<", ">=", "<=",
}


def _normalize_condition(condition: Any) -> Dict[str, Any]:
    """规整条件成 ``{"all": [{field, op, value}]}``;引擎只跑 all,故只存 all。
    每条子句必须有非空 field 与合法 op;缺 field 或 op 非法直接拒绝(抛 ValueError)。
    value 可选(present/absent/changed 等不需要)。"""
    if not isinstance(condition, dict):
        raise ValueError("condition must be an object")
    raw = condition.get("all")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("condition.all must be a list")
    out: List[Dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            raise ValueError("each condition clause must be an object")
        field = str(c.get("field") or "").strip()
        # op 先归一化别名(gte→>=…),与引擎 _cmp/agent 路 _canon_cond 一致。
        op = _norm_op(c.get("op"))
        if not field:
            raise ValueError("condition clause missing field")
        if op not in ALLOWED_OPS:
            raise ValueError(f"invalid condition op {op!r}")
        clause: Dict[str, Any] = {"field": field, "op": op}
        if "value" in c:
            clause["value"] = c.get("value")
        out.append(clause)
    return {"all": out}


def _normalize_actions(actions: Any) -> List[Dict[str, Any]]:
    """规整动作数组;每项为 dict,须有非空 kind(或 executor=='agent')。
    只保留 kind/executor/params/instruction + 可选 condition(分支 guard),其余剔除。
    guard 用与顶层 condition 相同的校验规整成 {"all":[...]};空/非法则剔除该键。
    非 list 抛 ValueError。"""
    if not isinstance(actions, list):
        raise ValueError("actions must be a list")
    out: List[Dict[str, Any]] = []
    for a in actions:
        if not isinstance(a, dict):
            raise ValueError("each action must be an object")
        kind = str(a.get("kind") or "").strip()
        executor = str(a.get("executor") or "").strip()
        if not kind and executor != "agent":
            raise ValueError("action missing kind (or executor=='agent')")
        item: Dict[str, Any] = {}
        if kind:
            item["kind"] = kind
        if executor:
            item["executor"] = executor
        params = a.get("params")
        if params is not None:
            if not isinstance(params, dict):
                raise ValueError("action params must be an object")
            item["params"] = params
        instr = a.get("instruction")
        if instr is not None:
            item["instruction"] = str(instr)
        # 可选分支 guard:与顶层 condition 同校验;空/非法则不写该键(动作无条件跑)。
        if "condition" in a:
            try:
                guard = _normalize_condition(a.get("condition"))
            except ValueError:
                guard = None
            if guard and guard.get("all"):
                item["condition"] = guard
        out.append(item)
    return out


def update_workflow_spec(db: DbWrapper, workflow_id: int, patch: Dict[str, Any]) -> bool:
    """结构化局部更新工作流(供前端点选块编辑 trigger/condition/actions)。
    只更新 patch 中出现的键;校验失败抛 ValueError(路由转 400)。返回是否命中。

    可改键:name(非空 str)、trigger_on('create'/'update'/None)、
    schedule_cron(str|None)、condition(对象,规整成 {"all":[...]})、actions(数组)。
    """
    sets: List[str] = []
    params: List[Any] = []

    if "name" in patch:
        name = patch.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        sets.append("name = %s")
        params.append(name.strip())

    if "trigger_on" in patch:
        to = patch.get("trigger_on")
        if to is not None and to not in ("create", "update"):
            raise ValueError("trigger_on must be 'create', 'update', or null")
        sets.append("trigger_on = %s")
        params.append(to)

    if "schedule_cron" in patch:
        sc = patch.get("schedule_cron")
        if sc is not None and not isinstance(sc, str):
            raise ValueError("schedule_cron must be a string or null")
        sets.append("schedule_cron = %s")
        params.append(sc)

    if "condition" in patch:
        cond = _normalize_condition(patch.get("condition"))
        sets.append("condition = %s::jsonb")
        params.append(_json(cond))

    if "actions" in patch:
        acts = _normalize_actions(patch.get("actions"))
        sets.append("actions = %s::jsonb")
        params.append(_json(acts))

    if not sets:
        # 无可改键 —— 仍以是否命中判定(便于路由统一 404 语义)。
        row = db.fetchone(
            "SELECT id FROM workflows WHERE id = %s AND NOT deleted",
            (int(workflow_id),),
        )
        return row is not None

    sets.append("updated_at = now()")
    params.append(int(workflow_id))
    row = db.fetchone(
        f"UPDATE workflows SET {', '.join(sets)} "
        "WHERE id = %s AND NOT deleted RETURNING id",
        tuple(params),
    )
    db.commit()
    return row is not None


def due_schedule_workflows(db: DbWrapper, now) -> List[int]:
    """返回此刻 cron 到点、启用的 schedule 工作流 id。用 croniter 判断
    上次 last_run_at 之后是否有一个 fire 落在 (last, now]。"""
    out: List[int] = []
    try:
        rows = db.fetchall(
            "SELECT id, schedule_cron, last_run_at FROM workflows "
            "WHERE trigger_type = 'schedule' AND enabled AND NOT deleted "
            "AND status = 'live'"
        ) or []
    except Exception:
        return []
    try:
        from croniter import croniter
    except Exception:
        logger.warning("workflow_schedule_no_croniter")
        return []
    for r in rows:
        cron = r.get("schedule_cron")
        if not cron or not croniter.is_valid(str(cron)):
            continue
        base = r.get("last_run_at") or now.replace(year=now.year - 1)
        try:
            nxt = croniter(str(cron), base).get_next(type(now))
            if nxt <= now:
                out.append(int(r["id"]))
        except Exception:
            continue
    return out


# ── 执行 ────────────────────────────────────────────────────────────


def _record_pk(module_name: str) -> str:
    return f"{module_name}_id"


def _load_record(db: DbWrapper, module_name: str, record_id: int) -> Optional[Dict[str, Any]]:
    pk = _record_pk(module_name)
    try:
        row = db.fetchone(
            f'SELECT * FROM "{module_name}" WHERE {pk} = %s', (record_id,))
    except Exception:
        return None
    if not row:
        return None
    d = dict(row)
    if str(d.get("deleted")) in ("1", "t", "true", "True"):
        return None
    return d


def _webhook_allowlist() -> set:
    """出站域名白名单(env WORKFLOW_WEBHOOK_ALLOWLIST,逗号分隔)。空=webhook 禁用。"""
    raw = os.environ.get("WORKFLOW_WEBHOOK_ALLOWLIST", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _url_is_safe(url: str) -> tuple:
    """webhook 出站 URL 安全校验。返回 (ok, host_or_reason)。
    规则:scheme∈http/https + host 命中白名单 + DNS 解析后非私网/环回(防 SSRF)。"""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
    except Exception:
        return False, "bad url"
    if u.scheme not in ("http", "https"):
        return False, f"scheme {u.scheme!r} not allowed"
    host = (u.hostname or "").lower()
    if not host:
        return False, "no host"
    allow = _webhook_allowlist()
    if not allow:
        return False, "webhook allowlist empty (set WORKFLOW_WEBHOOK_ALLOWLIST)"
    if not any(host == d or host.endswith("." + d) for d in allow):
        return False, f"host {host!r} not in allowlist"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return False, f"dns failed: {e}"
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            return False, f"resolves to non-public ip {info[4][0]}"
    return True, host


def _execute_engine_action(
    db: DbWrapper, action: Dict[str, Any], wf: Dict[str, Any],
    record: Dict[str, Any], actor_id: int,
) -> Dict[str, Any]:
    from hermes.crm.save import Save
    from hermes.crm.models.modules_db import ModulesDb

    kind = str(action.get("kind") or "")
    params = action.get("params") or {}
    module_name = wf["module"]
    record_id = int(record.get(_record_pk(module_name)) or 0)

    if kind == "update_field":
        field = str(params.get("field") or "")
        value = _render(params.get("value"), record)
        if not field:
            return {"kind": kind, "ok": False, "error": "missing field"}
        mod = ModulesDb(db).get_module_by_name(module_name)
        # 撤销前置:写之前抓字段旧值(可能为 None),供 revert_field 反向写回。
        old_value = record.get(field)
        Save(db).set_module(int(mod["id"])).set_record_id(record_id) \
            .set_user(actor_id, True).set_source("workflow") \
            .update({field: value})
        return {"kind": kind, "ok": True, "field": field, "value": value,
                "undo": {"type": "revert_field", "module": module_name,
                         "record_id": record_id, "changes": {field: old_value}}}

    if kind == "create_task":
        mod = ModulesDb(db).get_module_by_name("tasks")
        data: Dict[str, Any] = {
            "tasks_name": _render(params.get("title") or "工作流任务", record),
            "related_module": module_name,
            "related_record_id": record_id,
        }
        if params.get("owner_by") is not None:
            data["owner_by"] = params["owner_by"]
        elif record.get("owner_by") is not None:
            data["owner_by"] = record["owner_by"]
        if params.get("priority"):
            data["priority"] = params["priority"]
        if params.get("description"):
            data["description"] = _render(params["description"], record)
        new_id = Save(db).set_module(int(mod["id"])).set_user(actor_id, True) \
            .set_source("workflow").insert(data)
        # 撤销:删掉新建的 tasks 行(delete_row,表名=模块名)。
        return {"kind": kind, "ok": True, "task_id": new_id,
                "undo": {"type": "delete_row", "table": "tasks", "id": new_id}}

    if kind == "create_record":
        target = str(params.get("module") or "")
        mod = ModulesDb(db).get_module_by_name(target)
        if not mod:
            return {"kind": kind, "ok": False, "error": f"unknown module {target!r}"}
        data = {k: _render(v, record) for k, v in (params.get("data") or {}).items()}
        new_id = Save(db).set_module(int(mod["id"])).set_user(actor_id, True) \
            .set_source("workflow").insert(data)
        # 撤销:删掉新建的目标模块行(delete_row,表名=目标模块名)。
        return {"kind": kind, "ok": True, "module": target, "id": new_id,
                "undo": {"type": "delete_row", "table": target, "id": new_id}}

    if kind == "call_webhook":
        # 对外集成原语:把记录推到白名单内的外部 URL。安全见 _url_is_safe。
        import httpx
        url = str(params.get("url") or "")
        ok, info = _url_is_safe(url)
        if not ok:
            return {"kind": kind, "ok": False, "error": f"blocked: {info}"}
        method = str(params.get("method") or "POST").upper()
        headers = params.get("headers") if isinstance(params.get("headers"), dict) else {}
        payload = {
            "workflow": wf.get("name"),
            "module": module_name,
            "record_id": record_id,
            "trigger": wf.get("_trigger"),
            "record": {k: v for k, v in record.items() if not str(k).startswith("_")},
        }
        try:
            resp = httpx.request(
                method, url, json=payload, headers=headers,
                timeout=10.0, follow_redirects=False,
            )
            # 出站副作用不可逆:标 undoable=False,undo=None。
            return {"kind": kind, "ok": resp.status_code < 400,
                    "host": info, "status": resp.status_code,
                    "resp": resp.text[:200], "undoable": False, "undo": None}
        except Exception as e:  # noqa: BLE001 — 出站失败不抛,记进 run
            return {"kind": kind, "ok": False, "host": info,
                    "error": f"request failed: {e}", "undoable": False, "undo": None}

    if kind == "submit_approval":
        # 把记录提交进审批流(per-module opt-in:未配链则 skipped=no_config)。
        from hermes.crm.approval.engine import ApprovalEngine

        mod = ModulesDb(db).get_module_by_name(module_name)
        if not mod:
            return {"kind": kind, "ok": False,
                    "error": f"unknown module {module_name!r}", "undo": None}
        res = ApprovalEngine(db).submit_for_record(int(mod["id"]), record_id, actor_id)
        if not res.get("submitted"):
            return {"kind": kind, "ok": True, "skipped": res.get("skipped"), "undo": None}
        # best-effort 给审批人推渠道通知(失败不阻断工作流)。
        try:
            from hermes_cloud.app.crm.approval_notify import notify_approvers
            notify_approvers(db, int(res["approval_id"]))
        except Exception:
            pass
        return {"kind": kind, "ok": True, "approval_id": res["approval_id"],
                "undo": {"type": "cancel_approval", "approval_id": res["approval_id"]}}

    return {"kind": kind, "ok": False, "error": f"unknown engine kind {kind!r}", "undo": None}


def _execute_agent_action(
    wf: Dict[str, Any], record: Dict[str, Any], action: Dict[str, Any],
    actor_id: int,
) -> Dict[str, Any]:
    """判断/沟通动作:让 per-profile agent 跑一轮。

    走云的 ``/internal/utility/run-skill``(loopback 免 key);web 进程据此用
    ``run_utility_skill`` 拉起 profile agent 执行 instruction(已带触发记录上下文)。
    """
    import httpx

    module_name = wf["module"]
    record_id = record.get(_record_pk(module_name))
    params = action.get("params") or {}
    instruction = _render(str(params.get("instruction") or ""), record)
    skill = str(params.get("skill") or "crm-workflow-action")
    # 给 agent 触发上下文:哪条记录、关键字段
    ctx_fields = {k: v for k, v in record.items()
                  if not k.endswith(("_at", "_id")) or k == _record_pk(module_name)}
    full_instruction = (
        f"[工作流『{wf.get('name')}』触发于 {module_name} #{record_id}] "
        f"{instruction}\n\n触发记录字段:{ctx_fields}"
    )
    url = (os.environ.get("HERMES_CLOUD_INTERNAL_URL")
           or "http://127.0.0.1:8765").rstrip("/") + "/internal/utility/run-skill"
    try:
        resp = httpx.post(url, json={
            "user_id": actor_id,
            "skill_name": skill,
            "instruction": full_instruction,
            "timeout_sec": float(params.get("timeout_sec") or 90.0),
        }, timeout=float(params.get("timeout_sec") or 90.0) + 15.0)
    except Exception as e:
        return {"kind": "agent_task", "ok": False, "error": f"dispatch failed: {e}"}
    if resp.status_code != 200:
        return {"kind": "agent_task", "ok": False,
                "error": f"HTTP {resp.status_code}", "body": resp.text[:300]}
    # agent 的真实文字输出在 /run-skill 的 {"reply": ...}(run_utility_skill 终响)。
    # 既落 reply 全文供审计,也截一段 summary 方便 Runs 列表速览。
    reply = None
    try:
        reply = resp.json().get("reply")
    except Exception:
        pass
    out: Dict[str, Any] = {"kind": "agent_task", "ok": True, "reply": reply, "undo": None}
    if isinstance(reply, str) and reply.strip():
        out["summary"] = reply.strip()[:200]
    return out


def run_workflow(
    workflow_id: int,
    record_id: Optional[int],
    trigger: str,
    *,
    force: bool = False,
    cascade_depth: int = 0,
    changed_fields: Optional[Iterable[str]] = None,
    run_key: Optional[str] = None,
) -> Dict[str, Any]:
    """执行一条工作流(celery 调)。加载 → 防重 → 复核条件 → 逐动作执行 → 记 run。

    ``force=True``(test-run):绕过 status='live' 闸 + 绕过去重,供 draft 反复试跑。
    ``cascade_depth``:入站级联深度,超 MAX_CASCADE 则 skip(防 A→B→A 链)。本次运行
    期间把深度写进 contextvar,其动作引发的下游写在入队时读取并 +1。
    """
    # 级联深度入站闸(test-run 不计入级联,force 时跳过)——
    # 即便超限也落一条 skipped run,让 Runs 里能看见被拦。
    _cascade_depth.set(int(cascade_depth))
    with db_session() as db:
        if not force and cascade_depth > MAX_CASCADE:
            try:
                run = db.fetchone(
                    "INSERT INTO workflow_runs "
                    "(workflow_id, trigger_record_id, trigger, status, detail) "
                    "VALUES (%s, %s, %s, 'skipped', %s::jsonb) RETURNING id",
                    (int(workflow_id), record_id, trigger,
                     _json({"reason": "cascade_limit", "depth": int(cascade_depth)})))
                db.commit()
                logger.warning("workflow_cascade_limit wf=%s depth=%s",
                               workflow_id, cascade_depth)
            except Exception:  # noqa: BLE001 — 记不下也不抛
                logger.debug("cascade_limit_run_record_failed", exc_info=True)
            return {"status": "skipped", "reason": "cascade_limit", "depth": int(cascade_depth)}

        # status='live' 闸:event/schedule 只跑 live;test-run(force)绕过试 draft。
        status_gate = "" if force else " AND status = 'live'"
        wf = db.fetchone(
            "SELECT * FROM workflows WHERE id = %s AND enabled AND NOT deleted"
            + status_gate,
            (int(workflow_id),))
        if not wf:
            return {"status": "skipped", "reason": "workflow_gone_or_disabled"}

        # 防重:同 工作流×触发实例(run_key)的成功执行只跑一次(挡 at-least-once 重复投递)。
        # run_key:event=每次写的 uuid、schedule=tick:record —— 区分"重复投递"与"不同触发"。
        # 缺 run_key(过渡期老任务)退回旧键 (工作流×记录×触发)。force(test-run)绕过。
        if not force:
            if run_key:
                dup = db.fetchone(
                    "SELECT 1 FROM workflow_runs WHERE workflow_id = %s "
                    "AND run_key = %s AND status = 'done'",
                    (int(workflow_id), str(run_key)))
            elif record_id is not None:
                dup = db.fetchone(
                    "SELECT 1 FROM workflow_runs WHERE workflow_id = %s "
                    "AND trigger_record_id = %s AND trigger = %s AND status = 'done'",
                    (int(workflow_id), int(record_id), trigger))
            else:
                dup = None
            if dup:
                return {"status": "skipped", "reason": "already_done"}

        def _record_skip(reason: str) -> Dict[str, Any]:
            # "触发了但没跑,这是为什么" —— 落一条 skipped run 让 Runs 可见。
            # already_done 不走这里(纯去重噪音,重投会刷屏)。
            try:
                db.execute(
                    "INSERT INTO workflow_runs "
                    "(workflow_id, trigger_record_id, trigger, status, detail) "
                    "VALUES (%s, %s, %s, 'skipped', %s::jsonb)",
                    (int(workflow_id), record_id, trigger, _json({"reason": reason})))
                db.commit()
            except Exception:  # noqa: BLE001 — 记不下也不抛
                logger.debug("skip_run_record_failed reason=%s", reason, exc_info=True)
            return {"status": "skipped", "reason": reason}

        module_name = wf["module"]
        record: Dict[str, Any] = {}
        if record_id is not None:
            rec = _load_record(db, module_name, int(record_id))
            if rec is None:
                return _record_skip("record_gone")
            record = rec
            if not evaluate_condition(record, wf.get("condition"), changed_fields):
                return _record_skip("condition_false")

        actor_id = int(wf.get("created_by") or 1)
        run = db.fetchone(
            "INSERT INTO workflow_runs (workflow_id, trigger_record_id, trigger, run_key, status) "
            "VALUES (%s, %s, %s, %s, 'running') RETURNING id",
            (int(workflow_id), record_id, trigger, run_key))
        run_id = int(run["id"])

        results: List[Dict[str, Any]] = []
        status = "done"
        for action in (wf.get("actions") or []):
            if not isinstance(action, dict):
                continue
            # 分支:动作自带 guard(condition,同顶层形状)为假 → 跳过该动作、记 skipped,
            # 不算失败。guard 也吃 changed_fields(同顶层),"仅当某字段变化"也能做分支。
            guard = action.get("condition")
            if isinstance(guard, dict) and guard and not evaluate_condition(record, guard, changed_fields):
                results.append({"kind": action.get("kind"), "ok": True,
                                "skipped": True, "reason": "guard_false"})
                continue
            executor = str(action.get("executor") or "engine")
            try:
                if executor == "agent":
                    r = _execute_agent_action(wf, record, action, actor_id)
                else:
                    r = _execute_engine_action(db, action, wf, record, actor_id)
            except Exception as e:  # 一个动作失败不阻断其余,但整体记 failed
                r = {"kind": action.get("kind"), "ok": False, "error": str(e)}
            results.append(r)
            if not r.get("ok"):
                status = "failed"

        detail: Dict[str, Any] = {"results": results}

        # 连续失败自动停用(S3,保守):本次 failed 且最近 MAX_CONSEC_FAILURES 次
        # 全 failed → enabled=false。把本 run 计入(它已 INSERT,需含本次状态)。
        auto_disabled = False
        if status == "failed":
            try:
                # 本 run 此刻 DB 里仍是 'running'(下面才 UPDATE 成 failed),故排除自身、
                # 取此前 K-1 次,再把本次(failed)算上,凑满 K 才判定。
                prev = db.fetchall(
                    "SELECT status FROM workflow_runs WHERE workflow_id = %s "
                    "AND id <> %s ORDER BY created_at DESC LIMIT %s",
                    (int(workflow_id), run_id, MAX_CONSEC_FAILURES - 1)) or []
                if (len(prev) >= MAX_CONSEC_FAILURES - 1
                        and all(str(r.get("status")) == "failed" for r in prev)):
                    db.execute(
                        "UPDATE workflows SET enabled = false, updated_at = now() "
                        "WHERE id = %s", (int(workflow_id),))
                    auto_disabled = True
                    detail["auto_disabled"] = True
                    logger.warning(
                        "workflow_auto_disabled wf=%s after %s consecutive failures",
                        workflow_id, MAX_CONSEC_FAILURES)
            except Exception:  # noqa: BLE001 — 自停用失败不影响 run 落库
                logger.debug("workflow_auto_disable_check_failed", exc_info=True)

        db.execute(
            "UPDATE workflow_runs SET status = %s, detail = %s::jsonb WHERE id = %s",
            (status, _json(detail), run_id))
        db.execute(
            "UPDATE workflows SET last_run_at = now() WHERE id = %s", (int(workflow_id),))
        db.commit()
        return {"status": status, "run_id": run_id, "results": results,
                "auto_disabled": auto_disabled}


def undo_workflow_run(db: DbWrapper, run_id: int) -> Dict[str, Any]:
    """撤销一次 run 的可逆动作:逐 result 读 undo 描述反向执行。

    - ``revert_field``:把字段写回旧值(旧值为 None 即写回 NULL)。
    - ``delete_row``:有 deleted 列则软删,否则硬删。
    返回 ``{ok, run_id, undone, skipped, failed}``;已撤销返回 409 形态。
    """
    import json
    from hermes.crm.save import Save
    from hermes.crm.models.modules_db import ModulesDb

    row = db.fetchone("SELECT * FROM workflow_runs WHERE id = %s", (int(run_id),))
    if not row:
        return {"ok": False, "reason": "not_found", "_status": 404}
    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            detail = {}
    if not isinstance(detail, dict):
        detail = {}
    if detail.get("undone_at"):
        return {"ok": False, "reason": "already_undone", "_status": 409}
    status = str(row.get("status") or "")
    if status not in ("done", "failed"):
        return {"ok": False, "reason": f"status {status!r} not undoable", "_status": 409}

    undone: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    actor_id = 1  # 撤销以系统身份写(绕权限,同引擎 set_user(.,True))
    wf_row = db.fetchone(
        "SELECT created_by FROM workflows WHERE id = %s", (row.get("workflow_id"),))
    if wf_row and wf_row.get("created_by") is not None:
        actor_id = int(wf_row["created_by"])

    for r in (detail.get("results") or []):
        if not isinstance(r, dict):
            continue
        undo = r.get("undo")
        if not undo:
            # 不可逆(webhook / 失败动作 / 未知)—— 记进 skipped。
            skipped.append({"kind": r.get("kind"),
                            "reason": "not undoable" if r.get("undoable") is False
                            else "no undo descriptor"})
            continue
        utype = str(undo.get("type") or "")
        try:
            if utype == "revert_field":
                module = str(undo.get("module") or "")
                rid = int(undo.get("record_id") or 0)
                changes = undo.get("changes") or {}
                mod = ModulesDb(db).get_module_by_name(module)
                if not mod:
                    raise ValueError(f"unknown module {module!r}")
                Save(db).set_module(int(mod["id"])).set_record_id(rid) \
                    .set_user(actor_id, True).set_source("workflow_undo") \
                    .update(dict(changes))
                undone.append({"type": utype, "module": module,
                               "record_id": rid, "changes": changes})
            elif utype == "delete_row":
                table = str(undo.get("table") or "")
                rid = int(undo.get("id") or 0)
                if not table or not rid:
                    raise ValueError("missing table/id")
                # 有 deleted 列(模块表都有)则软删,否则硬删。
                has_del = db.fetchone(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = 'deleted'", (table,))
                pk = f"{table}_id"
                if has_del:
                    db.execute(
                        f'UPDATE "{table}" SET deleted = true WHERE {pk} = %s', (rid,))
                else:
                    db.execute(f'DELETE FROM "{table}" WHERE {pk} = %s', (rid,))
                undone.append({"type": utype, "table": table, "id": rid,
                               "mode": "soft" if has_del else "hard"})
            elif utype == "cancel_approval":
                from hermes.crm.approval.engine import ApprovalEngine

                aid = int(undo.get("approval_id") or 0)
                ApprovalEngine(db).cancel(aid)
                undone.append({"type": utype, "approval_id": aid})
            else:
                skipped.append({"kind": r.get("kind"),
                                "reason": f"unknown undo type {utype!r}"})
        except Exception as e:  # noqa: BLE001 — 单个撤销失败不阻断其余
            failed.append({"type": utype, "error": str(e)})
            logger.warning("workflow_undo_action_failed run=%s type=%s err=%s",
                           run_id, utype, e)

    # 标记已撤销:把撤销结果并进 detail,并用 now() 填 undone_at(防重复撤销)。
    db.execute(
        "UPDATE workflow_runs SET detail = jsonb_set("
        "  jsonb_set(%s::jsonb, '{undo_result}', %s::jsonb),"
        "  '{undone_at}', to_jsonb(now()::text)) WHERE id = %s",
        (_json(detail),
         _json({"undone": undone, "skipped": skipped, "failed": failed}),
         int(run_id)))
    db.commit()
    return {"ok": True, "run_id": int(run_id),
            "undone": undone, "skipped": skipped, "failed": failed}


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)
