"""AI 字段填值服务 —— spec 2026-06-21-ai-attributes-design.md。

修饰式 AI 字段:field_type 仍是底层类型,field_config.ai_fill 配置填值。
填值经满配 agent(/internal/utility/run-skill, 技能 crm-ai-attribute),
写回经 Save + emit_business_write(source=agent:ai-attr:*),provenance 入 ai_meta。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional, Set

logger = logging.getLogger(__name__)

AI_OUTPUT_TYPES: Set[str] = {"text", "number", "picklist", "multiselect", "boolean"}

# 4 个 AI 填值模式
AI_FILL_MODES: Set[str] = {"classify", "summarize", "web_agent", "prompt"}

# mode ↔ 允许的 field_type 匹配表
# field_type 在写边界(seed._field / field_provisioning.canonical_field_type)已归一,
# 此处只认 canonical(legacy select/radio/multipicklist 已不复存在)。
_MODE_FIELD_TYPES: Dict[str, Set[str]] = {
    "classify":  {"picklist", "multiselect"},
    "summarize": {"text", "textarea"},
    "web_agent": {"text", "number", "currency"},
    "prompt":    {"text", "number", "currency"},
}


class AiFillError(ValueError):
    """ai_fill 配置非法 —— 路由层转 HTTP 400。"""


def validate_ai_fill(
    field_type: str,
    ai_fill: Dict[str, Any],
    module_field_names: Iterable[str],
) -> None:
    """校验 field_config.ai_fill。非法抛 AiFillError。"""
    if not isinstance(ai_fill, dict):
        raise AiFillError("ai_fill must be an object")

    # ── mode 校验（缺省 prompt，向后兼容旧配置）──────────────────────────────
    mode = str(ai_fill.get("mode") or "prompt").strip().lower()
    if mode not in AI_FILL_MODES:
        raise AiFillError(
            f"mode must be one of {sorted(AI_FILL_MODES)}, got {mode!r}"
        )
    ft = str(field_type or "").strip().lower()
    allowed_types = _MODE_FIELD_TYPES[mode]
    if ft not in allowed_types:
        raise AiFillError(
            f"mode {mode!r} not allowed on field_type {ft!r} "
            f"(allowed: {sorted(allowed_types)})"
        )

    # ── output_type（可选，仅校验合法性，不再强制 == field_type）────────────
    out = str(ai_fill.get("output_type") or "").strip().lower()
    if out and out not in AI_OUTPUT_TYPES:
        raise AiFillError(f"output_type must be one of {sorted(AI_OUTPUT_TYPES)}")

    # ── mission 必填(output_spec 已退役:输出形状由 mode+output_type+字段选项
    #    自动决定,不让用户写;参见 _output_guidance)──────────────────────────
    if not str(ai_fill.get("mission") or "").strip():
        raise AiFillError("mission is required")

    # ── inputs.record 形状校验(新配置的本记录字段+子明细开关) ───────────────
    rec_cfg = (ai_fill.get("inputs") or {}).get("record")
    if rec_cfg is not None and not isinstance(rec_cfg, dict):
        raise AiFillError("inputs.record must be an object")

    # ── inputs.fields 引用校验 ──────────────────────────────────────────────
    names = set(module_field_names or [])
    in_fields = ((ai_fill.get("inputs") or {}).get("fields")) or []
    if not isinstance(in_fields, list):
        raise AiFillError("inputs.fields must be a list")
    for f in in_fields:
        if str(f) not in names:
            raise AiFillError(f"inputs.fields references unknown field {f!r}")


_BOOKKEEPING = {"created_by", "updated_by", "created_at", "updated_at",
                "department_id", "deleted", "owner_by", "ai_meta"}


def _record_enabled(inputs: Dict[str, Any]) -> bool:
    """本记录字段 + 子明细的总开关。

    新配置读 inputs.record.enabled;旧配置无 record 键但有 fields 键(含空列表)
    时向后兼容视为开启。
    """
    rec = inputs.get("record")
    if isinstance(rec, dict) and "enabled" in rec:
        return bool(rec["enabled"])
    return "fields" in inputs


def _ai_fill_of(field_meta: Dict[str, Any]) -> Dict[str, Any]:
    cfg = field_meta.get("field_config") or {}
    if isinstance(cfg, str):
        import json
        try:
            cfg = json.loads(cfg)
        except (ValueError, TypeError):
            cfg = {}
    return (cfg or {}).get("ai_fill") or {}


def _derived_attrs(
    record: Dict[str, Any],
    db=None,
    module: Optional[str] = None,
) -> Dict[str, Any]:
    """互动派生属性，映射为中文 key。

    优先从 interactions 表实时计算（db+module 均提供时）；
    否则降级读 record 里已有字段（向后兼容旧桩值）；
    两者均无则返回 {}。

    db/module 拿不到时静默返回 {}，不抛错。
    """
    # ── 实时计算路径 ──────────────────────────────────────────────────────────
    if db is not None and module:
        pk = f"{module}_id"
        record_id = record.get(pk)
        if record_id is not None:
            try:
                from hermes_cloud.app.crm.interaction_derived import compute_derived
                derived = compute_derived(db, module, int(record_id))
                out: Dict[str, Any] = {}
                if derived.get("last_interaction_at") is not None:
                    out["最近互动"] = derived["last_interaction_at"]
                if derived.get("first_interaction_at") is not None:
                    out["首次互动"] = derived["first_interaction_at"]
                if "interaction_count" in derived:
                    out["互动次数"] = derived["interaction_count"]
                if "connection_strength" in derived:
                    out["连接强度"] = derived["connection_strength"]
                return out
            except Exception:
                logger.debug("_derived_attrs_compute_failed", exc_info=True)
                # 降级到 record 字段

    # ── 降级路径：从 record 读已有字段 ───────────────────────────────────────
    mapping = {
        "last_interaction_at": "最近互动",
        "first_interaction_at": "首次互动",
        "interaction_count": "互动次数",
        "connection_strength": "连接强度",
    }
    out: Dict[str, Any] = {}
    for src_key, cn_key in mapping.items():
        v = record.get(src_key)
        if v is not None:
            out[cn_key] = v
    return out


import re as _re

_SAFE_FIELD_RE = _re.compile(r'^[a-z_][a-z0-9_]*$')

# 子表/模块标识符白名单(允许大小写,与 field_writeback._IDENT_RE 一致)
_SAFE_IDENT = _re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

# 子明细每子模块最多带入的行数
_SUBDETAIL_ROW_LIMIT = 50


def _expand_product(db, product_id) -> Optional[Dict[str, Any]]:
    """查 products 记录整条业务字段(剔除簿记列/主键)。best-effort,失败返回 None。"""
    if product_id in (None, 0):
        return None
    try:
        row = db.fetchone(
            'SELECT * FROM "products" '
            'WHERE "products_id" = %s AND (deleted = false OR deleted IS NULL) LIMIT 1',
            (int(product_id),),
        )
    except Exception:
        logger.debug("subdetail_product_expand_failed", exc_info=True)
        return None
    if not row:
        return None
    return {k: v for k, v in row.items()
            if k not in _BOOKKEEPING and k != "products_id"}


def _build_subdetails(db, module: str, record_id) -> list:
    """采集当前记录的子明细行(每子模块最多 _SUBDETAIL_ROW_LIMIT 行)。

    - 经 collections.parent_module_id 找子模块;
    - 每子模块查 {parent}_id 匹配的行,剔除簿记列/PK/父FK;
    - detail_kind=='product' 的行:products_id 展开为整条产品字段(内联到 row["product"]);
    - 只 1 跳(产品记录自身关联字段不再穿透);
    - best-effort:任一步异常静默跳过。
    返回 [{"module","title","rows":[...]}, ...],无内容则 []。
    """
    if db is None or record_id is None:
        return []
    if not _SAFE_IDENT.match(module):
        return []
    try:
        children = db.fetchall(
            "SELECT id, name, title, detail_kind FROM collections "
            "WHERE parent_module_id = (SELECT id FROM collections WHERE name = %s) "
            "ORDER BY id",
            (module,),
        ) or []
    except Exception:
        logger.debug("subdetail_children_query_failed", exc_info=True)
        return []

    parent_col = f"{module}_id"
    out: list = []
    for ch in children:
        child = str(ch.get("name") or "")
        if not _SAFE_IDENT.match(child):
            continue
        try:
            rows = db.fetchall(
                f'SELECT * FROM "{child}" '
                f'WHERE "{parent_col}" = %s AND (deleted = false OR deleted IS NULL) '
                f'ORDER BY "{child}_id" LIMIT %s',
                (int(record_id), _SUBDETAIL_ROW_LIMIT),
            ) or []
        except Exception:
            logger.debug("subdetail_rows_query_failed", exc_info=True)
            continue
        is_product = str(ch.get("detail_kind") or "") == "product"
        skip_keys = {f"{child}_id", parent_col}
        if is_product:
            skip_keys.add("products_id")
        clean_rows: list = []
        for r in rows:
            row = {k: v for k, v in r.items()
                   if k not in _BOOKKEEPING and k not in skip_keys}
            if is_product:
                prod = _expand_product(db, r.get("products_id"))
                if prod is not None:
                    row["product"] = prod
            clean_rows.append(row)
        if clean_rows:
            out.append({"module": child,
                        "title": ch.get("title") or child,
                        "rows": clean_rows})
    return out


def _make_relation_resolver(
    db, module: str, record: Dict[str, Any]
) -> Callable[[str, str], Optional[str]]:
    """构建关系字段穿透 resolver（真穿透 + 降级兜底）。

    执行顺序：
    1. 降级路径（不查库）：
       - target_field 是 name/title/display_name 时，尝试 record[{rel_field}_display]；
       - 尝试 record[{rel_field}_{target_field}] 平铺字段。
       命中任一即返回，省一次查库。
    2. 真穿透（降级未命中时）：
       - 从 record.get(rel_field) 取关联记录 id；
       - 查当前模块 field_config.relation.target_module；
       - 防 SQL 注入：target_field 必须通过 ^[a-z_][a-z0-9_]*$ 正则验证，
         且必须是 target_module 的合法字段名（查 target_module fields 白名单）；
       - SELECT target_field FROM target_module WHERE {target_module}_id=%s
         AND (deleted=false OR deleted IS NULL) LIMIT 1。
    3. 任何异常静默返回 None（best-effort）。
    4. 模块字段 schema 在 resolver 闭包内用 dict 缓存，避免重复查库。
    """
    # 闭包级缓存：module_name -> List[field_row]
    _schema_cache: Dict[str, list] = {}

    def _get_module_fields(mod_name: str) -> list:
        if mod_name in _schema_cache:
            return _schema_cache[mod_name]
        try:
            from hermes.crm.models.modules_db import ModulesDb
            from hermes.crm.models.module_fields_db import ModuleFieldsDb
            mod = ModulesDb(db).get_module_by_name(mod_name)
            if not mod:
                _schema_cache[mod_name] = []
                return []
            fields = ModuleFieldsDb(db).get_module_fields(int(mod["id"])) or []
            _schema_cache[mod_name] = fields
            return fields
        except Exception:
            logger.debug("_make_relation_resolver_get_fields_failed", exc_info=True)
            _schema_cache[mod_name] = []
            return []

    def _get_target_module(rel_field: str) -> Optional[str]:
        """从当前模块的 field schema 找 rel_field 的 target_module。"""
        try:
            fields = _get_module_fields(module)
            for f in fields:
                if f.get("field_name") != rel_field:
                    continue
                cfg = f.get("field_config") or {}
                if isinstance(cfg, str):
                    try:
                        cfg = json.loads(cfg)
                    except (ValueError, TypeError):
                        cfg = {}
                rel_cfg = (cfg or {}).get("relation") or {}
                tm = rel_cfg.get("target_module")
                return str(tm) if tm else None
        except Exception:
            logger.debug("_make_relation_resolver_get_target_failed", exc_info=True)
        return None

    def _valid_target_field(target_field: str, target_module: str) -> bool:
        """防 SQL 注入：正则 + target_module 字段白名单双重校验。"""
        # 第一关：正则格式白名单
        if not _SAFE_FIELD_RE.match(target_field):
            return False
        # 第二关：必须是 target_module 的合法字段名
        try:
            fields = _get_module_fields(target_module)
            valid_names = {f.get("field_name") for f in fields if f.get("field_name")}
            # 允许主键列（target_module_id 不一定在 module_fields 里）
            valid_names.add(f"{target_module}_id")
            return target_field in valid_names
        except Exception:
            return False

    def resolver(rel_field: str, target_field: str) -> Optional[str]:
        try:
            # ── 1. 降级路径（不查库）────────────────────────────────────────────
            display_key = f"{rel_field}_display"
            if target_field in ("name", "title", "display_name"):
                v = record.get(display_key)
                if v is not None:
                    return str(v)
            flat_key = f"{rel_field}_{target_field}"
            v = record.get(flat_key)
            if v is not None:
                return str(v)

            # ── 2. 真穿透 ────────────────────────────────────────────────────────
            if db is None:
                return None

            rel_id = record.get(rel_field)
            if rel_id is None:
                return None

            target_module = _get_target_module(rel_field)
            if not target_module:
                return None

            # 防 SQL 注入双重校验
            if not _valid_target_field(target_field, target_module):
                logger.warning(
                    "relation_resolver_blocked_invalid_field rel=%s target_field=%s",
                    rel_field, target_field,
                )
                return None

            pk_col = f"{target_module}_id"
            row = db.fetchone(
                f'SELECT "{target_field}" FROM "{target_module}" '
                f'WHERE "{pk_col}" = %s AND (deleted = false OR deleted IS NULL) '
                f'LIMIT 1',
                (rel_id,),
            )
            if row is None:
                return None
            val = row.get(target_field)
            return str(val) if val is not None else None

        except Exception:
            logger.debug("relation_resolver_failed", exc_info=True)
            return None

    return resolver


def build_fill_context(*, db, module: str, record: Dict[str, Any],
                       field_meta: Dict[str, Any], user_id: Optional[int] = None):
    """构填值上下文。返回 (context_dict, basis_list)。db=None 时跳过会话查询。

    v2 扩展（M1-T4）：
    - 根据 mode 读取 ModeSpec，按需注入图谱上下文；
    - 对 mission 文本渲染 {} 变量（含关系穿透）；
    - needs_graph 模式：注入 search_memories 语义检索 + subgraph 实体子图。
    """
    from hermes_cloud.app.crm import ai_variables
    from hermes_cloud.app.crm.ai_modes import mode_spec

    af = _ai_fill_of(field_meta)
    target = str(field_meta.get("field_name") or "")
    inputs = af.get("inputs") or {}
    pk = f"{module}_id"

    # ── mode / spec ──────────────────────────────────────────────────────────
    mode = str(af.get("mode") or "prompt").strip().lower()
    try:
        spec = mode_spec(mode)
    except ValueError:
        spec = mode_spec("prompt")

    # ── 字段收集(record.enabled 网关;开启则一律全业务字段) ──────────────────
    rec_on = _record_enabled(inputs)
    if rec_on:
        fields = {
            k: v for k, v in record.items()
            if k not in _BOOKKEEPING and k != target and k != pk
            and not k.endswith("_display")
        }
    else:
        fields = {}
    basis = [f"field:{k}" for k in fields.keys()]

    # ── 变量渲染（mission 文本） ──────────────────────────────────────────────
    context: Dict[str, Any] = {"fields": fields}
    prompt_text = str(af.get("mission") or "").strip()
    if prompt_text:
        rendered, var_basis = ai_variables.render(
            prompt_text,
            record=record,
            relation_resolver=_make_relation_resolver(db, module, record),
            derived=_derived_attrs(record, db=db, module=module),
        )
        context["rendered_prompt"] = rendered
        basis.extend(var_basis)

    # ── interactions（保持原有逻辑） ──────────────────────────────────────────
    interactions = []
    inter_cfg = inputs.get("interactions") or {}
    record_id = record.get(pk)
    if db is not None and inter_cfg.get("enabled") and record_id is not None:
        limit = int(inter_cfg.get("limit") or 20)
        import json as _json
        rows = db.fetchall(
            "SELECT id, kind, content, source, occurred_at FROM interactions "
            "WHERE subjects @> %s::jsonb ORDER BY occurred_at DESC LIMIT %s",
            (_json.dumps([{"module": module, "id": int(record_id)}]), limit),
        ) or []
        for r in rows:
            interactions.append({
                "id": r.get("id"), "kind": r.get("kind"),
                "content": r.get("content"), "source": r.get("source"),
            })
            basis.append(f"interaction:{r.get('id')}")
    context["interactions"] = interactions

    # ── 子明细(record.enabled 时带入;产品明细行展开产品全字段) ───────────────
    subdetails: list = []
    if db is not None and rec_on and record_id is not None:
        subdetails = _build_subdetails(db, module, record_id)
        for s in subdetails:
            basis.append(f"subdetail:{s['module']}")
    context["subdetails"] = subdetails

    # ── 图谱注入（仅 needs_graph 模式：classify / summarize） ─────────────────
    if db is not None and spec.needs_graph:
        # 语义检索
        search_query = (
            context.get("rendered_prompt")
            or prompt_text
            or str(record.get("name") or record.get(f"{module}_name") or "")
        )
        if search_query:
            try:
                from hermes.memory.vector_store import search_memories
                rows = search_memories(
                    query=search_query,
                    user_id=user_id,
                    top_k=5,
                    db=db,
                )
                context["semantic"] = rows or []
                for row in (rows or []):
                    mem_id = row.get("id")
                    if mem_id is not None:
                        basis.append(f"semantic:{mem_id}")
            except Exception:
                logger.debug("search_memories_failed", exc_info=True)

        # 实体子图
        entity_id = record.get("memory_entity_id")
        if entity_id is not None:
            try:
                from hermes.memory.entities import subgraph
                graph = subgraph(entity_id=int(entity_id), depth=1, db=db)
                context["subgraph"] = {
                    "entities": graph.get("entities") or [],
                    "edges": graph.get("edges") or [],
                }
                basis.append(f"graph:entity:{entity_id}")
            except Exception:
                logger.debug("subgraph_failed", exc_info=True)

    return context, basis


_CONTRACT = (
    '\n\n返回严格 JSON,不要额外文字:\n'
    '{"value": <符合产出规格的值,或 null>, "confidence": <0..1 数字>,\n'
    ' "basis": [<引用的 field:/interaction: 指针字符串>],\n'
    ' "abstained": <true/false>, "reason": <弃填或低置信原因,或 null>}\n'
    '信息不足以可靠判定时:abstained=true、value=null,绝不臆造。'
)


_WEB_AGENT_HINT = (
    "[联网] 用 web_search 工具联网调研所需外部信息;"
    "每条结论必须带来源 URL,写进 basis;"
    "查不到则 abstained=true,绝不编造。"
)


def _output_guidance(field_meta, af, spec) -> str:
    """据 mode + output_type + 字段自身选项自动生成"输出要求",替代用户手写
    output_spec(Attio 的 AI 属性没有产出规格,由系统按字段定义判断)。"""
    out = _effective_output_type(af, spec)
    if out == "picklist":
        opts = [o for o in str(field_meta.get("options") or "").split(",") if o]
        base = (
            "把记录归类到以下选项之一,只返回选项值本身(不要解释):\n"
            f"{', '.join(opts) if opts else '(该字段暂无选项)'}"
        )
        if af.get("allow_new_options"):
            base += "\n若已有选项均不适用,可返回一个简短的新选项名。"
        return base
    if out == "number":
        return "只返回一个数字(纯数值,不带单位、货币符号或解释文字)。"
    # text 类:按模式给倾向
    mode = str(af.get("mode") or "prompt").strip().lower()
    if mode == "summarize":
        return "返回简洁准确的纯文本摘要,直接给结论,不要多余前缀或解释。"
    if mode == "web_agent":
        return "返回查到的关键事实(简洁文本),并在 basis 里附来源 URL;查不到则如实说明、不要编造。"
    return "返回简洁的文本结果,直接给内容,不要无关说明。"


def _build_instruction(field_meta, context):
    import json
    from hermes_cloud.app.crm.ai_modes import mode_spec as _mode_spec
    af = _ai_fill_of(field_meta)
    # mode 缺省时按输出类型推断(picklist/multiselect→classify,否则 prompt),
    # 这样即便上游漏传 mode,分类字段也走分类指令而非通用 prompt。
    mode = str(af.get("mode") or "").strip().lower()
    if mode not in ("classify", "summarize", "web_agent", "prompt"):
        ot = str(af.get("output_type") or "").strip().lower()
        mode = "classify" if ot in ("picklist", "multiselect") else "prompt"
    try:
        spec = _mode_spec(mode)
    except ValueError:
        spec = _mode_spec("prompt")

    parts = [f"[模式:{mode}] {spec.instruction_template}"]

    # 使命：优先用已渲染变量的版本，否则用原始文本
    rendered = context.get("rendered_prompt")
    mission_text = rendered if rendered else af.get("mission", "")
    parts.append(f"[使命] {mission_text}")

    parts += [
        f"[上下文与规则] {af.get('context_rules','') or '(无额外规则)'}",
        f"[输出要求] {_output_guidance(field_meta, af, spec)}",
    ]
    if context.get("fields"):
        parts.append(
            f"[本记录字段] {json.dumps(context['fields'], ensure_ascii=False, default=str)}"
        )

    inter = context.get("interactions") or []
    if inter:
        parts.append(f"[关联会话(近→远)] {json.dumps(inter, ensure_ascii=False, default=str)[:6000]}")

    subs = context.get("subdetails") or []
    if subs:
        subs_json = json.dumps(subs, ensure_ascii=False, default=str)
        if len(subs_json) > 8000:
            subs_json = subs_json[:8000] + "…(截断)"
        parts.append(f"[子明细] {subs_json}")

    semantic = context.get("semantic")
    if semantic:
        parts.append(f"[相关记忆] {json.dumps(semantic, ensure_ascii=False, default=str)[:4000]}")

    subgraph = context.get("subgraph")
    if subgraph:
        parts.append(f"[实体关系图] {json.dumps(subgraph, ensure_ascii=False, default=str)[:3000]}")

    if mode == "web_agent":
        parts.append(_WEB_AGENT_HINT)

    return "\n".join(parts) + _CONTRACT


def _effective_output_type(af: dict, spec, field_type: str = "") -> str:
    """决定 coerce 使用的 output_type。
    优先 af.get('output_type');否则**按字段真实类型推导**(coerce 必须匹配物理列类型:
    currency→number、multiselect→multiselect、picklist→picklist、boolean→boolean、
    text/textarea→text);再缺才回退 spec.coerce_kind。
    """
    explicit = str(af.get("output_type") or "").strip().lower()
    if explicit:
        return explicit
    if field_type:
        from hermes_cloud.app.crm.field_provisioning import canonical_field_type
        ft = canonical_field_type(str(field_type).strip().lower())
        if ft == "multiselect":
            return "multiselect"
        if ft == "picklist":
            return "picklist"
        if ft in ("number", "currency"):
            return "number"
        if ft == "boolean":
            return "boolean"
        if ft in ("text", "textarea"):
            return "text"
    kind = str(spec.coerce_kind or "text").lower()
    if kind == "option":
        return "picklist"
    if kind == "number":
        return "number"
    return "text"


def _abstain(reason: str):
    return {"value": None, "confidence": 0.0, "basis": [], "abstained": True, "reason": reason}


def _dispatch_via_http(user_id, instruction, timeout_sec):
    """httpx 直发 /internal/utility/run-skill(同 workflows._execute_agent_action)。"""
    import os, httpx
    url = (os.environ.get("HERMES_CLOUD_INTERNAL_URL") or "http://127.0.0.1:8765").rstrip("/") \
        + "/internal/utility/run-skill"
    try:
        resp = httpx.post(url, json={
            "user_id": int(user_id), "skill_name": "crm-ai-attribute",
            "instruction": instruction, "timeout_sec": timeout_sec,
        }, timeout=timeout_sec + 15.0)
    except Exception as e:
        logger.warning("ai_fill_dispatch_http_failed err=%s", e)
        return None
    if resp.status_code != 200:
        logger.warning("ai_fill_dispatch_http_status=%s", resp.status_code)
        return None
    try:
        return resp.json().get("reply")
    except Exception:
        return None


def dispatch_fill(*, user_id, field_meta, context, timeout_sec: float = 90.0):
    """派发满配 agent 跑 crm-ai-attribute,返回解析后 dict(含 abstained)。"""
    instruction = _build_instruction(field_meta, context)
    reply = _dispatch_via_http(int(user_id), instruction, timeout_sec)
    if not isinstance(reply, dict):
        return _abstain("no_or_unparseable_reply")
    reply.setdefault("abstained", False)
    reply.setdefault("confidence", 0.0)
    reply.setdefault("basis", [])
    reply.setdefault("reason", None)
    return reply


def _coerce_value(output_type, value, options_csv):
    """按底层类型强转校验。返回 (ok, coerced)。"""
    if value is None:
        return False, None
    ot = (output_type or "").lower()
    if ot == "number":
        try:
            return True, float(value)
        except (TypeError, ValueError):
            return False, None
    if ot == "boolean":
        if isinstance(value, bool):
            return True, value
        s = str(value).strip().lower()
        if s in ("true", "1", "是", "yes"):
            return True, True
        if s in ("false", "0", "否", "no"):
            return True, False
        return False, None
    if ot == "picklist":
        opts = [o for o in (options_csv or "").split(",") if o]
        return (str(value) in opts), str(value)
    if ot == "multiselect":
        opts = [o.strip() for o in (options_csv or "").split(",") if o.strip()]
        toks = [t.strip() for t in str(value).split(",") if t.strip()]
        if not toks:
            return False, None
        if opts and any(t not in opts for t in toks):
            return False, None
        return True, ",".join(toks)
    return True, str(value)


def _src(module):
    prof = str(module.get("profile") or "default")
    return f"agent:ai-attr:{prof}"


def _meta_val(v):
    return v if isinstance(v, (str, int, float, bool)) else str(v)


def _same_value(a, b) -> bool:
    """Loose equality: numeric-aware (Decimal/float/int across boundaries), else str compare."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def apply_fill(db, *, module, record_id, field_meta, result, actor_id, trigger):
    from hermes.crm.save import Save

    module_name = str(module["name"])
    field = str(field_meta["field_name"])
    pk = f"{module_name}_id"
    af = _ai_fill_of(field_meta)
    min_conf = float(af.get("min_confidence") or 0.0)
    now_iso = datetime.now(timezone.utc).isoformat()

    def _write_meta(meta):
        db.execute(
            f'UPDATE "{module_name}" SET ai_meta = '
            f"jsonb_set(COALESCE(ai_meta, '{{}}'::jsonb), %s, %s::jsonb) "
            f'WHERE {pk} = %s',
            ("{" + field + "}", json.dumps(meta, ensure_ascii=False, default=str), int(record_id)),
        )

    def _abstain_write(reason, conf=0.0):
        with db.transaction():
            _write_meta({"source": _src(module), "confidence": conf,
                         "generated_at": now_iso, "basis": result.get("basis") or [],
                         "written_value": None, "abstained": True, "reason": reason})
        try:
            from hermes_cloud.app.interactions import emit as emit_interaction
            emit_interaction(kind="ai_abstain",
                             subjects=[{"module": module_name, "id": int(record_id)}],
                             actors=[{"type": "user", "id": int(actor_id)}],
                             content={"field": field, "reason": reason}, source=_src(module), db=db)
        except Exception:
            logger.debug("ai_abstain_emit_skipped", exc_info=True)
        return {"ok": False, "abstained": True, "reason": reason, "field": field}

    if result.get("abstained"):
        return _abstain_write(str(result.get("reason") or "abstained"))
    conf = float(result.get("confidence") or 0.0)
    if conf < min_conf:
        return _abstain_write("low_confidence", conf)
    # coerce：output_type 优先用显式配置，否则按 mode_spec.coerce_kind 推导
    from hermes_cloud.app.crm.ai_modes import mode_spec as _mode_spec
    _mode = str(af.get("mode") or "prompt").strip().lower()
    try:
        _spec = _mode_spec(_mode)
    except ValueError:
        _spec = _mode_spec("prompt")
    ok, coerced = _coerce_value(
        _effective_output_type(af, _spec, str(field_meta.get("field_type") or "")),
        result.get("value"), field_meta.get("options")
    )
    if not ok:
        return _abstain_write("invalid_value", conf)

    if trigger != "manual":
        cur = db.fetchone(f'SELECT {field} AS v, ai_meta FROM "{module_name}" WHERE {pk}=%s', (record_id,))
        if cur:
            prev = ((cur.get("ai_meta") or {}).get(field) or {}).get("written_value")
            cur_v = cur.get("v")
            if prev is not None and not _same_value(cur_v, prev):
                return {"ok": False, "skipped": "human_override", "field": field}

    Save(db).set_module(int(module["id"])).set_record_id(int(record_id)) \
        .set_user(int(actor_id), True).set_source(_src(module)).update({field: coerced})
    # NOTE(v1): value 与 ai_meta 分两笔事务(Save.update 自成事务 + 此处)。极窄窗口内
    # 进程崩溃可能 value 已落、ai_meta 缺失 → 该值看起来像人工录入(无徽标),且下次
    # 事件触发会因 prev=None 而覆盖。可接受(需崩溃在窗口内);原子化需把 ai_meta 串入
    # Save 路径,留作后续。
    with db.transaction():
        _write_meta({"source": _src(module), "confidence": conf, "generated_at": now_iso,
                     "basis": result.get("basis") or [], "written_value": _meta_val(coerced),
                     "abstained": False, "reason": None})
    return {"ok": True, "value": coerced, "field": field, "confidence": conf}


def fill_record(*, module_name, record_id, fields=None, trigger="manual", actor_id=None):
    from hermes.crm.db import db_session
    from hermes.crm.models.modules_db import ModulesDb
    from hermes.crm.models.module_fields_db import ModuleFieldsDb

    results = []
    with db_session() as db:
        module = ModulesDb(db).get_module_by_name(module_name)
        if not module:
            return {"status": "skipped", "reason": "unknown_module", "results": []}
        pk = f"{module_name}_id"
        rec = db.fetchone(f'SELECT * FROM "{module_name}" WHERE {pk}=%s', (int(record_id),))
        if not rec or str(rec.get("deleted")) in ("1", "t", "true", "True"):
            return {"status": "skipped", "reason": "record_gone", "results": []}
        rec = dict(rec)
        all_fields = ModuleFieldsDb(db).get_module_fields(int(module["id"])) or []
        ai_fields = [f for f in all_fields if (_ai_fill_of(f) or {}).get("enabled")]
        if fields:
            ai_fields = [f for f in ai_fields if f["field_name"] in set(fields)]
        actor = int(actor_id or rec.get("owner_by") or rec.get("created_by") or 1)

        for fm in ai_fields:
            fname = fm["field_name"]
            if trigger != "manual" and _recent_run(db, module_name, record_id, fname, trigger):
                results.append({"field": fname, "skipped": "deduped"})
                continue
            ctx, basis = build_fill_context(db=db, module=module_name, record=rec, field_meta=fm, user_id=actor)
            result = dispatch_fill(user_id=actor, field_meta=fm, context=ctx)
            if not result.get("basis"):
                result["basis"] = basis
            r = apply_fill(db, module=module, record_id=int(record_id),
                           field_meta=fm, result=result, actor_id=actor, trigger=trigger)
            _record_run(db, module_name, record_id, fname, trigger, r)
            results.append(r)
    return {"status": "done", "results": results}


_AI_FIELDS_CACHE = {}   # module_name -> (expires_monotonic, [(field_name, ai_fill_dict)])


def ai_fields_for_module(db, module_name: str):
    import time as _time
    now = _time.monotonic()
    hit = _AI_FIELDS_CACHE.get(module_name)
    if hit and hit[0] > now:
        return hit[1]
    out = []
    try:
        from hermes.crm.models.modules_db import ModulesDb
        from hermes.crm.models.module_fields_db import ModuleFieldsDb
        mod = ModulesDb(db).get_module_by_name(module_name)
        if mod:
            for f in (ModuleFieldsDb(db).get_module_fields(int(mod["id"])) or []):
                af = _ai_fill_of(f)
                if af.get("enabled"):
                    out.append((f["field_name"], af))
    except Exception:
        return []
    _AI_FIELDS_CACHE[module_name] = (now + 60.0, out)
    return out


def _recent_run(db, module, record_id, field, trigger, window_sec: int = 30) -> bool:
    row = db.fetchone(
        "SELECT 1 FROM ai_fill_runs WHERE module=%s AND record_id=%s AND field_name=%s "
        "AND trigger=%s AND status='done' AND created_at > now() - (%s || ' seconds')::interval LIMIT 1",
        (module, int(record_id), field, trigger, str(window_sec)))
    return bool(row)


def _record_run(db, module, record_id, field, trigger, result) -> None:
    with db.transaction():
        db.execute(
            "INSERT INTO ai_fill_runs (module, record_id, field_name, trigger, status, detail) "
            "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
            (module, int(record_id), field, trigger, "done",
             json.dumps(result, ensure_ascii=False, default=str)))


_DESIGN_CONTRACT = (
    '\n\n返回严格 JSON,不要额外文字:\n'
    '{"output_type":"text|number|picklist|boolean","label":"<简短中文字段名>",\n'
    ' "mission":"<使命:让AI判断/生成什么>","context_rules":"<判断依据与规则,可空字符串>",\n'
    ' "output_spec":"<限定只能返回什么>","options":["<picklist时的取值>"...],\n'
    ' "with_interactions":<true/false 是否需要客户会话上下文>}\n'
    'output_type 非 picklist 时 options 省略或空数组。label 简短(<=8字)。'
)


def design_field_config(*, user_id, intent, module, field_names, timeout_sec: float = 60.0):
    """把用户一句话需求展开成 AI 字段配置 dict;失败返回 None。"""
    instruction = (
        "[任务] 帮用户设计一个 CRM『AI 自动填充字段』的配置(只产出配置,不要真的去填值)。\n"
        f"[用户的一句话需求] {intent}\n"
        f"[该模块({module})现有字段] {list(field_names)}\n"
        "[要求] 据需求推断:输出类型、简短中文字段名、三段式 prompt(mission/context_rules/output_spec)、"
        "picklist 时给 options、是否需要客户会话上下文。规则里可引用上面的现有字段名。"
        + _DESIGN_CONTRACT
    )
    reply = _dispatch_via_http(int(user_id), instruction, timeout_sec)
    if not isinstance(reply, dict):
        return None
    return reply
