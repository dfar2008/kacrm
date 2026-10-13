"""存量 AI 字段迁移 —— AI Attributes 4 模式重构 M1-T9。

两类存量需要修补：
1. 旧 ``field_config.ai_fill`` 无 mode：按字段类型 + 配置内容推断正确 mode。
2. 旧 ``ai_field`` 类型字段（如有）：field_type 改为真实输出类型，确保 ai_fill 有 mode。

默认干跑（dry-run）：只打印要改什么；``apply=True`` 才写库。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── mode → 允许 field_type（与 ai_attributes.py 保持一致）──────────────────
_CLASSIFY_TYPES = {"picklist", "multiselect"}
_WEB_AGENT_TYPES = {"text", "number", "currency"}
_PROMPT_TYPES    = {"text", "number", "currency", "textarea"}

# 联网/调研关键词：出现在 mission 或 output_spec 里视为需要 web_agent
_WEB_KEYWORDS = (
    "联网", "查", "最近", "融资", "动态", "网上", "搜索", "实时", "官网",
    "新闻", "舆情", "调研",
)


def infer_mode(field_type: str, ai_fill: dict) -> str:
    """按字段类型 + ai_fill 配置推断最合适的 mode。

    规则（优先级从高到低）：
    1. picklist / multiselect → "classify"
    2. text/number/currency + 联网迹象（关键词 or inputs.web）→ "web_agent"
    3. 其余 → "prompt"
    """
    ft = str(field_type or "").strip().lower()

    # 规则 1：分类型字段
    if ft in _CLASSIFY_TYPES:
        return "classify"

    # 规则 2：联网迹象（仅适用于 web_agent 兼容的字段类型）
    if ft in _WEB_AGENT_TYPES:
        mission     = str(ai_fill.get("mission")     or "")
        output_spec = str(ai_fill.get("output_spec") or "")
        combined    = mission + output_spec

        # 关键词命中
        if any(kw in combined for kw in _WEB_KEYWORDS):
            return "web_agent"

        # inputs.web 存在
        inputs = ai_fill.get("inputs") or {}
        if isinstance(inputs, dict) and inputs.get("web"):
            return "web_agent"

    # 默认
    return "prompt"


def migrate_ai_fill(field_type: str, ai_fill: dict) -> Tuple[str, dict]:
    """幂等迁移单个字段的 (field_type, ai_fill)。

    - 若 ai_fill 已有合法 mode，原样返回。
    - 若 field_type == "ai_field"，把 field_type 改成 ai_fill.output_type（缺省 "text"）。
    - 补全 mode。

    返回 (new_field_type, new_ai_fill)。
    """
    from hermes_cloud.app.crm.ai_attributes import AI_FILL_MODES, _MODE_FIELD_TYPES

    ft = str(field_type or "").strip().lower()
    af = dict(ai_fill)  # 浅拷贝，不改原始对象

    # ── 旧 ai_field 类型 → 修正为真实输出类型 ─────────────────────────────
    if ft == "ai_field":
        output_type = str(af.get("output_type") or "text").strip().lower()
        # output_type 可能不是合法 field_type（如 "boolean"），取常见映射
        ft = output_type if output_type in {"text", "number", "currency",
                                            "picklist", "multiselect", "textarea"} else "text"

    # ── 已有合法 mode 且与 field_type 兼容 → 幂等，直接返回 ────────────────
    existing_mode = str(af.get("mode") or "").strip().lower()
    if existing_mode in AI_FILL_MODES:
        allowed = _MODE_FIELD_TYPES.get(existing_mode, set())
        if ft in allowed:
            return ft, af  # 已经合法，原样

    # ── 推断并写入 mode ────────────────────────────────────────────────────
    af["mode"] = infer_mode(ft, af)
    return ft, af


# ── Runner ──────────────────────────────────────────────────────────────────

def run_migration(db, *, apply: bool = False) -> List[Dict[str, Any]]:
    """扫描所有带 ai_fill 的 module_fields 行，补 mode（默认 dry-run）。

    参数
    ----
    db      : DbWrapper（hermes.crm.db.DbWrapper），由调用方传入（避免嵌套事务）。
    apply   : False（默认）= 只打印，不写库；True = 实际 UPDATE。

    返回
    ----
    changes : List[dict]，每项 = {id, module_id, field_name,
                                   old_field_type, new_field_type,
                                   old_mode, new_mode}
              仅包含「有变化」的行。
    """
    # 拉取所有有 ai_fill 的字段（field_config 非空且内含 ai_fill key）
    # psycopg3 中 JSONB 列返回 Python dict；但也可能是 str（旧驱动），需 coerce
    rows = db.fetchall(
        "SELECT id, module_id, field_name, field_type, field_config "
        "FROM module_fields "
        "WHERE field_config IS NOT NULL",
    )

    changes: List[Dict[str, Any]] = []

    for row in rows:
        fc = row.get("field_config") or {}
        if isinstance(fc, str):
            try:
                fc = json.loads(fc)
            except Exception:
                continue
        if not isinstance(fc, dict):
            continue

        af = fc.get("ai_fill")
        if not isinstance(af, dict):
            continue
        if not af.get("enabled"):
            continue  # 未启用的 ai_fill 跳过

        old_ft   = str(row.get("field_type") or "")
        old_mode = str(af.get("mode") or "")

        new_ft, new_af = migrate_ai_fill(old_ft, af)
        new_mode = str(new_af.get("mode") or "")

        # 判断是否真的有变化
        if new_ft == old_ft and new_mode == old_mode:
            continue

        entry = {
            "id":            row["id"],
            "module_id":     row["module_id"],
            "field_name":    row.get("field_name"),
            "old_field_type": old_ft,
            "new_field_type": new_ft,
            "old_mode":      old_mode,
            "new_mode":      new_mode,
        }
        changes.append(entry)

        action = "APPLY" if apply else "DRY-RUN"
        logger.info(
            "[ai_migrate] %s id=%s field=%s type %s→%s mode %r→%r",
            action, row["id"], row.get("field_name"),
            old_ft, new_ft, old_mode, new_mode,
        )

        if apply:
            new_fc = dict(fc)
            new_fc["ai_fill"] = new_af
            db.execute(
                "UPDATE module_fields SET field_type=%s, field_config=%s WHERE id=%s",
                (new_ft, json.dumps(new_fc, ensure_ascii=False), row["id"]),
            )

    if not changes:
        logger.info("[ai_migrate] no changes needed (dry_run=%s)", not apply)
    else:
        logger.info(
            "[ai_migrate] %d field(s) %s",
            len(changes),
            "updated" if apply else "would be updated (dry-run)",
        )

    return changes
