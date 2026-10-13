"""CRM 审批 → 渠道通知推送。

提交审批后,给每个审批人的 IM(飞书/企微/微信/钉钉)推一段**文本**通知,
含待审批记录信息 + 「回复 同意/拒绝」指引。审批人在 IM 直接回复即可处理
(回复审批由渠道 CRM agent 的 crm_approve_chain_step/reject 完成,无需新增)。

复用:
- ``insights.notify._post_via_cloud`` — 统一推送原语(POST /internal/channel/push)。
- profile ``home_channel``(``platforms.<chan>.home_channel = {platform, chat_id}``)。
- user→profile:``profiles.id == users.username``;profile_dir = ~/.hermes/profiles/<id>。

best-effort:下游 bot 挂了/审批人无渠道绑定 → 记日志跳过,绝不阻断提交。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from hermes.crm.db import DbWrapper

logger = logging.getLogger(__name__)


def _profile_id_for_user(db: DbWrapper, user_id: int) -> Optional[str]:
    """profiles.id == users.username(Charter v2 §简化原则)。"""
    row = db.fetchone("SELECT username FROM users WHERE id = %s", (int(user_id),))
    uname = (row or {}).get("username") if row else None
    return str(uname).strip() if uname else None


def _get_home_channels(profile_dir: Path) -> List[Tuple[str, str]]:
    """读 profile config,返回**全部** (platform, chat_id) home_channel。

    一个用户可能绑多个渠道(飞书/企微/钉钉/微信)。调用方按序尝试,
    推到第一个成功的为止(钉钉 Stream Mode 不能主动推 → 自动跳到下一个)。
    """
    from hermes_cloud.app.channel_config import read_profile_config

    out: List[Tuple[str, str]] = []
    try:
        data = read_profile_config(profile_dir)
    except Exception:
        return out
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        return out
    for chan, entry in platforms.items():
        if not isinstance(entry, dict):
            continue
        home = entry.get("home_channel")
        if isinstance(home, dict):
            platform = str(home.get("platform") or chan).strip()
            chat_id = str(home.get("chat_id") or "").strip()
            if platform and chat_id:
                out.append((platform, chat_id))
    return out


def _parse_csv_ids(csv: object) -> List[int]:
    out: List[int] = []
    for p in str(csv or "").split(","):
        p = p.strip()
        if p:
            try:
                out.append(int(p))
            except ValueError:
                pass
    return out


# 详情块里跳过的系统/内部字段(无审批价值或会噪声)。
_SKIP_FIELDS = {
    "created_at", "updated_at", "created_by", "modified_by", "deleted",
    "approval_status", "is_top", "top_order", "sequence", "tenant_id",
    "owner_department", "data_source",
}


def _fmt_amount(val) -> Optional[str]:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f"¥{f:,.0f}" if f == int(f) else f"¥{f:,.2f}"


def _resolve_relation(db: DbWrapper, field_name: str, val) -> Optional[str]:
    """关联字段(如 accounts_id → accounts.accounts_name)解析成名称。"""
    if not field_name.endswith("_id"):
        return None
    tbl = field_name[:-3]
    try:
        r = db.fetchone(
            f"SELECT {tbl}_name AS nm FROM {tbl} WHERE {tbl}_id = %s", (int(val),)
        )
        return (r.get("nm") if r else None) or None
    except Exception:
        return None


def _resolve_user(db: DbWrapper, val) -> Optional[str]:
    try:
        r = db.fetchone(
            "SELECT realname, username FROM users WHERE id = %s", (int(val),)
        )
        return (r.get("realname") or r.get("username")) if r else None
    except Exception:
        return None


def _basic_info_lines(
    db: DbWrapper, module_name: str, table_name: str, row: dict
) -> list:
    """遍历模块字段,把所有有值的业务字段显示出来(关联/负责人解析成名称)。"""
    try:
        fields = db.fetchall(
            "SELECT mf.field_name, mf.field_label, mf.field_type "
            "FROM module_fields mf JOIN modules m ON m.id = mf.module_id "
            "WHERE m.name = %s ORDER BY mf.id",
            (module_name,),
        ) or []
    except Exception:
        return []
    name_col = f"{table_name}_name"
    out: list = []
    for f in fields:
        fn = f.get("field_name")
        if not fn or fn == name_col or fn in _SKIP_FIELDS:
            continue
        val = row.get(fn)
        if val is None or val == "" or val == 0:
            continue
        label = f.get("field_label") or fn
        ftype = (f.get("field_type") or "").lower()
        disp: Optional[str] = None
        if ftype == "relation":
            disp = _resolve_relation(db, fn, val)
            if not disp:
                continue  # 关联解析不到就不显示裸 id
        elif ftype == "user":
            disp = _resolve_user(db, val)
            if not disp:
                continue
        elif ftype in ("number", "currency", "money", "float", "decimal"):
            if ftype in ("currency", "money") or "amount" in fn or "price" in fn:
                disp = _fmt_amount(val)
            else:
                try:
                    fv = float(val)
                    disp = str(int(fv)) if fv == int(fv) else str(fv)
                except (TypeError, ValueError):
                    disp = str(val)
        elif ftype in ("textarea", "text"):
            s = str(val).strip()
            if not s:
                continue
            disp = (s[:60] + "…") if len(s) > 60 else s
        else:
            disp = str(val)
        if disp:
            out.append(f"{label}：{disp}")
    return out


def _line_items(db: DbWrapper, module_name: str, table_name: str, data_id: int) -> list:
    """产品明细子明细(quotes/salesorders 的 {table}_products)。best-effort。"""
    if module_name not in ("salesorders", "quotes"):
        return []
    try:
        return db.fetchall(
            f"SELECT products_name, model, specifications, quantity, price, "
            f"       subtotal, total "
            f"FROM {table_name}_products "
            f"WHERE {table_name}_id = %s AND (deleted = FALSE OR deleted IS NULL) "
            f"ORDER BY {table_name}_products_id",
            (int(data_id),),
        ) or []
    except Exception:
        return []


def _generic_subdetails_lines(
    db: DbWrapper, module_name: str, table_name: str, data_id: int
) -> list:
    """通用子明细(collections.detail_kind='generic',parent_module_id=本模块)。"""
    try:
        kids = db.fetchall(
            "SELECT c.name, c.title FROM collections c "
            "JOIN modules m ON m.id = c.parent_module_id "
            "WHERE m.name = %s AND c.detail_kind = 'generic'",
            (module_name,),
        ) or []
    except Exception:
        return []
    out: list = []
    for k in kids:
        child = k.get("name")
        if not child:
            continue
        try:
            rows = db.fetchall(
                f'SELECT * FROM "{child}" WHERE {table_name}_id = %s '
                f"AND (deleted = FALSE OR deleted IS NULL) "
                f'ORDER BY "{child}_id"',
                (int(data_id),),
            ) or []
        except Exception:
            continue
        if not rows:
            continue
        out.append(f"{k.get('title') or child}（{len(rows)} 项）：")
        for r in rows[:10]:
            nm = r.get(f"{child}_name")
            if not nm:  # 没有 name 列时取第一个非系统文本值
                for kk, vv in r.items():
                    if kk.endswith("_id") or kk in _SKIP_FIELDS:
                        continue
                    if isinstance(vv, str) and vv.strip():
                        nm = vv
                        break
            out.append(f"  · {nm or '(明细)'}")
        if len(rows) > 10:
            out.append(f"  …等共 {len(rows)} 项")
    return out


def _record_details(
    db: DbWrapper, module_name: str, table_name: str, data_id: int, module_label: str
) -> str:
    """审批通知的记录详情块:记录名 + 全部有值业务字段 + 产品明细 + 通用子明细。

    方便审批人不点开就能看清要批什么。全程 best-effort,任何字段缺失/异常都跳过。
    """
    try:
        row = db.fetchone(
            f'SELECT * FROM "{table_name}" WHERE {table_name}_id = %s', (int(data_id),)
        ) or {}
    except Exception:
        row = {}
    name = row.get(f"{table_name}_name") or f"记录{data_id}"
    lines = [f"{module_label}：{name}"]

    # 基本信息:所有有值业务字段(客户/联系人/状态/日期/金额/负责人…)
    lines.extend(_basic_info_lines(db, module_name, table_name, row))

    # 产品明细(子明细):产品名[规格] ×数量 单价 = 小计
    items = _line_items(db, module_name, table_name, data_id)
    if items:
        lines.append(f"产品明细（{len(items)} 项）：")
        for it in items[:15]:
            seg = f"  · {it.get('products_name') or '产品'}"
            spec = it.get("specifications") or it.get("model")
            if spec:
                seg += f"（{str(spec).strip()}）"
            if it.get("quantity") is not None:
                seg += f" ×{it['quantity']}"
            price = _fmt_amount(it.get("price")) if it.get("price") is not None else None
            if price:
                seg += f" 单价{price}"
            amt = it.get("total") if it.get("total") is not None else it.get("subtotal")
            amt_s = _fmt_amount(amt) if amt is not None else None
            if amt_s:
                seg += f" = {amt_s}"
            lines.append(seg)
        if len(items) > 15:
            lines.append(f"  …等共 {len(items)} 项")

    # 通用子明细(generic collections)
    lines.extend(_generic_subdetails_lines(db, module_name, table_name, data_id))

    return "\n".join(lines)


def _build_text(details: str, approval_id: int) -> str:
    # 审批单号用 SP-<id> 格式(明显区别于订单号 #<id>,避免 agent 混淆/误批)。
    # 用户回复『同意 SP-158』即可精确处理这一条,多条待审批也不乱。
    return (
        f"🔔 你有一条待审批\n"
        f"{details}\n"
        f"审批单号 SP-{approval_id} —— 回复『同意 SP-{approval_id}』或"
        f"『拒绝 SP-{approval_id} + 理由』即可处理本单。"
    )


def notify_approvers(
    db: DbWrapper, approval_id: int, *, hermes_home: Optional[Path] = None
) -> Dict[str, int]:
    """给审批 ``approval_id`` 当前 pending 步的每个审批人推渠道通知。

    返回 ``{"pushed": n, "skipped": m}``。best-effort,不抛。
    """
    from hermes_cloud.app.insights.notify import _post_via_cloud

    pushed = 0
    skipped = 0
    appr = db.fetchone(
        "SELECT a.id, a.module_id, a.data_id, a.current_step, "
        "       m.name AS module_name, m.table_name, m.label AS module_label "
        "FROM approvals a JOIN modules m ON m.id = a.module_id "
        "WHERE a.id = %s AND a.status = 'pending'",
        (int(approval_id),),
    )
    if not appr:
        return {"pushed": 0, "skipped": 0}

    step = db.fetchone(
        "SELECT approver_ids FROM approval_steps "
        "WHERE approval_id = %s AND step_order = %s",
        (int(approval_id), int(appr["current_step"])),
    )
    approver_ids = _parse_csv_ids(step.get("approver_ids") if step else "")
    details = _record_details(
        db, str(appr["module_name"]), str(appr["table_name"]),
        int(appr["data_id"]), str(appr.get("module_label") or appr["module_name"]),
    )
    text = _build_text(details, int(approval_id))
    base = hermes_home or (Path.home() / ".hermes")

    for uid in approver_ids:
        try:
            pid = _profile_id_for_user(db, uid)
            if not pid:
                skipped += 1
                continue
            channels = _get_home_channels(base / "profiles" / pid)
            if not channels:
                skipped += 1
                continue
            # 按序尝试,推到第一个成功的渠道为止(钉钉 Stream Mode 失败→下一个)。
            delivered = False
            for platform, chat_id in channels:
                ok = _post_via_cloud(
                    user_id=uid, profile_id=pid,
                    platform=platform, channel_id=chat_id, text=text,
                )
                if ok:
                    delivered = True
                    break
            pushed += 1 if delivered else 0
            skipped += 0 if delivered else 1
        except Exception:
            logger.exception(
                "approval notify failed approval=%s approver=%s", approval_id, uid
            )
            skipped += 1
    return {"pushed": pushed, "skipped": skipped}
