"""provision_workflow — 工作流落库的单一真相。

dispatch 的 crm_workflow(action="create")(管理员直建)与
/crm/field_proposals/{id}/review(非管理员提案审批通过)都走这里,
确保两条路插出来的 workflows 行结构一致、归属字段一致。
"""
from __future__ import annotations

import json
from typing import Any, Dict

from hermes.crm.db import DbWrapper


def _canon_cond(cond: Any) -> Any:
    """把 condition 里子句的 op 统一成引擎符号(agent 可能写 gte/lt/eq…)。
    顶层 condition + 每个 action 自己的 condition(分支 guard)都过一遍。"""
    from hermes_cloud.app.crm.workflows import _norm_op
    if not isinstance(cond, dict):
        return cond
    for key in ("all", "any"):
        clauses = cond.get(key)
        if isinstance(clauses, list):
            for c in clauses:
                if isinstance(c, dict) and "op" in c:
                    c["op"] = _norm_op(c.get("op"))
    return cond


def provision_workflow(db: DbWrapper, spec: Dict[str, Any], actor: Dict[str, Any]) -> Dict[str, Any]:
    """插一行 workflows。``actor`` 决定 created_by/updated_by(归属)。

    spec: {name, trigger_type, module, trigger_on?, schedule_cron?, condition?, actions}
    调用方负责已校验 module 存在、cron 合法、event 触发有 trigger_on。
    """
    ttype = str(spec["trigger_type"])
    aid = int(actor.get("id") or 0)
    # op 正规化:顶层 condition + 每个 action 的分支 guard 的 op 统一成引擎符号。
    condition = _canon_cond(spec.get("condition") or {})
    actions = spec.get("actions") or []
    for a in actions:
        if isinstance(a, dict) and isinstance(a.get("condition"), dict):
            a["condition"] = _canon_cond(a["condition"])
    row = db.fetchone(
        "INSERT INTO workflows (name, trigger_type, module, trigger_on, "
        "schedule_cron, condition, actions, created_by, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) RETURNING id",
        (str(spec["name"]), ttype, str(spec["module"]),
         spec.get("trigger_on") if ttype == "event" else None,
         str(spec["schedule_cron"]) if ttype == "schedule" else None,
         json.dumps(condition),
         json.dumps(actions),
         aid, aid),
    )
    db.commit()
    return {"id": int(row["id"])}
