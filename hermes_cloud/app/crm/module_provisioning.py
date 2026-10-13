"""模块供给 —— 新建顶层自定义模块的唯一正确路径(spec 2026-06-14)。

镜像 hermes_cloud/app/crm/field_provisioning.py 的模式,把「加列」升到「建表」:
一个事务内 建表 + 注册 collections + 灌系统/初始字段 + 写默认权限 + 写 changelog。
事件溯源写路径(P1)对新模块记录自动生效——它们经 Save 同事务+记忆复利路径。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import HTTPException

from hermes.crm.db import DbWrapper
from hermes.crm.models.module_fields_db import ModuleFieldsDb
from hermes.crm.models.schema_changelog_db import SchemaChangelogDb
from hermes_cloud.app.crm.field_provisioning import provision_field


# 模块名直接进 DDL(表名),必须是安全标识符:小写蛇形。
_IDENT_RE = re.compile(r"[a-z][a-z0-9_]*")

# PG 保留字 + 内部前缀,禁止做模块名。
_RESERVED = {
    "select", "insert", "update", "delete", "table", "from", "where",
    "user", "users", "group", "order", "index", "drop", "create", "alter",
    "join", "into", "values", "set", "and", "or", "not", "null",
}


def _normalize_initial_field(f: dict) -> dict:
    """把 agent 的 initial_field 形状归一成 provision_field 期望的形状。

    Agent(crm_schema create_module)产出 ``{name, label, type, target}``,
    而 provision_field 要 ``{field_name, field_label, field_type, field_config}``。
    两边长期不一致会让"建模块带初始字段/审批建模块"在 provision_field 处
    报 'field_name… required'。这里做防御式归一(canonical 形状原样通过)。
    """
    if not isinstance(f, dict):
        return f
    g = dict(f)
    if not g.get("field_name") and g.get("name"):
        g["field_name"] = g["name"]
    if not g.get("field_label") and g.get("label"):
        g["field_label"] = g["label"]
    if not g.get("field_type") and g.get("type"):
        g["field_type"] = g["type"]
    # relation: agent 给 target,provision_field 要 field_config.relation.target_module
    tgt = g.get("target") or g.get("target_module")
    if str(g.get("field_type") or "") == "relation" and tgt and not g.get("field_config"):
        g["field_config"] = {"relation": {"target_module": tgt}}
    return g


# ---------------------------------------------------------------------------
# 命名校验
# ---------------------------------------------------------------------------

def _validate_module_name(db: DbWrapper, name: str) -> None:
    """拒绝非法/撞名/保留字模块名。失败抛 HTTP 400/409。"""
    if not name or not _IDENT_RE.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail=f"invalid module name {name!r}: must be [a-z][a-z0-9_]*",
        )
    # PG 标识符上限 63 字节;表名 + ``trg_<name>_updated_at`` /
    # ``idx_<name>_owner`` 后缀须留余量,否则静默截断 → 触发器/索引名碰撞。
    if len(name) > 40:
        raise HTTPException(
            status_code=400,
            detail=f"module name too long ({len(name)}>40): {name!r}",
        )
    if name in _RESERVED or name.startswith("pg_"):
        raise HTTPException(status_code=400, detail=f"reserved name: {name!r}")
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM collections WHERE name = %s", (name,)
    )
    if int((row or {}).get("n") or 0) > 0:
        raise HTTPException(status_code=409, detail=f"module exists: {name!r}")
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s",
        (name,),
    )
    if int((row or {}).get("n") or 0) > 0:
        raise HTTPException(status_code=409, detail=f"table exists: {name!r}")


# ---------------------------------------------------------------------------
# 物理表 DDL(系统列模板,与 alembic 0004 record table 约定一致)
# ---------------------------------------------------------------------------

def _create_record_table(db: DbWrapper, name: str) -> None:
    """建模块物理表:标准系统列 + 索引 + set_updated_at 触发器。

    查询构造器硬依赖 `<name>_id` 主键与 `<name>_name` headline 命名约定。
    """
    db.execute(
        f'''
        CREATE TABLE "{name}" (
            "{name}_id"   BIGSERIAL PRIMARY KEY,
            "{name}_name" VARCHAR(255) NOT NULL,
            owner_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_by    BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by    BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            deleted       BOOLEAN NOT NULL DEFAULT FALSE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        '''
    )
    db.execute(
        f'CREATE INDEX "idx_{name}_owner" ON "{name}"(owner_by) '
        f"WHERE deleted = FALSE"
    )
    db.execute(
        f'CREATE INDEX "idx_{name}_alive" ON "{name}"("{name}_id") '
        f"WHERE deleted = FALSE"
    )
    db.execute(
        f'CREATE TRIGGER "trg_{name}_updated_at" BEFORE UPDATE ON "{name}" '
        f"FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


# ---------------------------------------------------------------------------
# 系统字段元数据 + 默认权限
# ---------------------------------------------------------------------------

def _system_field_specs(name: str) -> list[dict]:
    """系统字段:列已由 _create_record_table 建出,这里只灌 module_fields 元数据。"""
    return [
        {"field_name": f"{name}_name", "field_label": "名称",
         "field_type": "text", "is_required": True, "sort_order": 0},
        {"field_name": "owner_by", "field_label": "负责人",
         "field_type": "user", "sort_order": 90},
        {"field_name": "created_at", "field_label": "创建时间",
         "field_type": "datetime", "is_readonly": True, "sort_order": 99},
    ]


def _seed_system_fields(db: DbWrapper, module_id: int, name: str) -> None:
    mf = ModuleFieldsDb(db)
    for spec in _system_field_specs(name):
        mf.insert_field({
            "module_id": module_id,
            "field_name": spec["field_name"],
            "field_label": spec["field_label"],
            "field_type": spec["field_type"],
            "is_required": bool(spec.get("is_required", False)),
            "is_system": True,
            "is_readonly": bool(spec.get("is_readonly", False)),
            "is_searchable": False,
            "sort_order": int(spec.get("sort_order", 0)),
            "options": None,
            "default_value": None,
            "description": None,
            "field_config": json.dumps({"is_system": True}),
        })


def _grant_default_perms(db: DbWrapper, module_id: int) -> None:
    """给所有现有角色发该模块的 index/view/create/update 权限。

    保守默认:新模块对所有业务角色可见可写(否则非 admin 看不到——见
    Crm.tsx has_access 过滤)。删除/导出等高危权限不默认发。
    """
    role_ids = [int(r["id"]) for r in db.fetchall("SELECT id FROM roles")]
    perm_ids = [int(r["id"]) for r in db.fetchall(
        "SELECT id FROM permissions WHERE code IN "
        "('index','view','create','update')")]
    for rid in role_ids:
        for pid in perm_ids:
            db.execute(
                "INSERT INTO role_permissions "
                "(role_id, permission_id, module_id, has_permission) "
                "VALUES (%s, %s, %s, TRUE) ON CONFLICT DO NOTHING",
                (rid, pid, module_id),
            )


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def provision_module(db: DbWrapper, spec: dict, actor: dict,
                     reason: Optional[str]) -> dict:
    """建一个顶层自定义模块:表+注册+系统/初始字段+默认权限+changelog,原子。"""
    name = str(spec.get("name") or "").strip()
    title = str(spec.get("title") or name).strip()
    title_en = spec.get("title_en")
    initial_fields = spec.get("initial_fields") or []

    _validate_module_name(db, name)

    with db.transaction():
        _create_record_table(db, name)
        db.execute(
            "INSERT INTO collections (name, title, title_en, parent_module_id, "
            "detail_kind) VALUES (%s, %s, %s, NULL, NULL)",
            (name, title, title_en),
        )
        row = db.fetchone("SELECT id FROM collections WHERE name = %s", (name,))
        module_id = int(row["id"])

        _seed_system_fields(db, module_id, name)

        module = {"id": module_id, "name": name, "table_name": name}
        for f in initial_fields:
            provision_field(db, module, _normalize_initial_field(f), actor, reason)

        _grant_default_perms(db, module_id)

        SchemaChangelogDb(db).insert_entry(
            actor_type="user",
            actor_id=int(actor["id"]),
            actor_label=actor.get("username"),
            module_id=module_id,
            module_name=name,
            field_id=None,
            field_name=None,
            op="add_module",
            before=None,
            after={"title": title, "title_en": title_en},
            reason=reason,
        )

    return {"module_id": module_id, "name": name}


def rename_module(db: DbWrapper, name: str, title: Optional[str],
                  title_en: Optional[str], actor: dict,
                  reason: Optional[str]) -> dict:
    """只改显示名(title/title_en),绝不动物理表名/列名。"""
    row = db.fetchone(
        "SELECT id, title, title_en FROM collections WHERE name = %s", (name,))
    if not row:
        raise HTTPException(status_code=404, detail=f"unknown module: {name!r}")
    new_title = title if title is not None else row["title"]
    new_title_en = title_en if title_en is not None else row["title_en"]
    with db.transaction():
        db.execute(
            "UPDATE collections SET title = %s, title_en = %s WHERE name = %s",
            (new_title, new_title_en, name))
        SchemaChangelogDb(db).insert_entry(
            actor_type="user", actor_id=int(actor["id"]),
            actor_label=actor.get("username"),
            module_id=int(row["id"]), module_name=name,
            field_id=None, field_name=None, op="rename_module",
            before={"title": row["title"], "title_en": row["title_en"]},
            after={"title": new_title, "title_en": new_title_en},
            reason=reason)
    return {"name": name, "title": new_title, "title_en": new_title_en}



# ---------------------------------------------------------------------------
# 子明细(子模块):带 parent_module_id 的模块,内联在父记录,不进主菜单
# ---------------------------------------------------------------------------

_DETAIL_KINDS = {"product", "generic"}


def _create_subdetail_table(db: DbWrapper, name: str, parent: str) -> None:
    """建子明细物理表:<name>_id PK + <parent>_id 软引用 + 审计列 + 触发器。

    子明细行归属父记录,不单独发权/不要 owner;业务字段由字段 builder 追加。
    """
    db.execute(
        f'CREATE TABLE "{name}" ('
        f'"{name}_id" BIGSERIAL PRIMARY KEY, '
        f'"{parent}_id" BIGINT NOT NULL, '
        f'deleted BOOLEAN NOT NULL DEFAULT FALSE, '
        f'created_at TIMESTAMPTZ NOT NULL DEFAULT now(), '
        f'updated_at TIMESTAMPTZ NOT NULL DEFAULT now())'
    )
    db.execute(f'CREATE INDEX "idx_{name}_parent" ON "{name}"("{parent}_id")')
    db.execute(
        f'CREATE TRIGGER "trg_{name}_updated_at" BEFORE UPDATE ON "{name}" '
        f'FOR EACH ROW EXECUTE FUNCTION set_updated_at()'
    )


def provision_subdetail(db: DbWrapper, parent_name: str, spec: dict,
                        actor: dict, reason: Optional[str]) -> dict:
    """给某模块挂一个子明细(product / generic)。继承父权限,不进主菜单。"""
    name = str(spec.get("name") or "").strip()
    title = str(spec.get("title") or name).strip()
    title_en = spec.get("title_en")
    kind = str(spec.get("detail_kind") or "generic").strip()
    initial_fields = spec.get("initial_fields") or []

    if kind not in _DETAIL_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"detail_kind must be one of {_DETAIL_KINDS}")
    _validate_module_name(db, name)
    prow = db.fetchone("SELECT id FROM collections WHERE name = %s "
                       "AND parent_module_id IS NULL", (parent_name,))
    if not prow:
        raise HTTPException(status_code=404,
                            detail=f"unknown parent module: {parent_name!r}")
    parent_id = int(prow["id"])

    with db.transaction():
        _create_subdetail_table(db, name, parent_name)
        db.execute(
            "INSERT INTO collections (name, title, title_en, parent_module_id, "
            "detail_kind) VALUES (%s, %s, %s, %s, %s)",
            (name, title, title_en, parent_id, kind),
        )
        row = db.fetchone("SELECT id FROM collections WHERE name = %s", (name,))
        module_id = int(row["id"])

        module = {"id": module_id, "name": name, "table_name": name}
        for f in initial_fields:
            provision_field(db, module, _normalize_initial_field(f), actor, reason)

        SchemaChangelogDb(db).insert_entry(
            actor_type="user", actor_id=int(actor["id"]),
            actor_label=actor.get("username"),
            module_id=module_id, module_name=name,
            field_id=None, field_name=None, op="add_subdetail",
            before=None,
            after={"title": title, "parent": parent_name, "detail_kind": kind},
            reason=reason)

    return {"module_id": module_id, "name": name,
            "parent_module_id": parent_id, "detail_kind": kind}


# ---------------------------------------------------------------------------
# 模块回收(deprovision)—— 安全删除 + 持久归档 + 还原
#
# 镜像 provision_*:provisioning 的逆操作。系统原本只有"建"没有"删",这里补上
# 生产级删除路径:删前做依赖检查(核心模块保护 / 入向 relation / 写回规则),
# 整模块快照进 module_archive(可还原),再在一个事务内级联清除。
# ---------------------------------------------------------------------------

# 内置核心模块,永不可删(无 is_system 标记,只能靠白名单;对齐 seed_metadata)。
PROTECTED_MODULES = frozenset({
    "accounts", "contacts", "leads", "potentials", "quotes", "salesorders",
    "tasks", "products", "receivables", "payments", "expenses", "projects",
})

# 删前要探存在性的脏数据残留表(stock 功能已 revert,但表可能仍在某些库)。
_RESIDUE_TABLES = ("stock_movements", "stock_postings")


def _assert_safe_ident(name: str) -> None:
    """DDL 纵深防御:即便表名来自 DB,进 DDL 前仍校验是安全标识符。"""
    if not name or not _IDENT_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail=f"unsafe identifier: {name!r}")


def _table_exists(db: DbWrapper, table: str) -> bool:
    row = db.fetchone(
        "SELECT 1 AS x FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s", (table,))
    return bool(row)


def _find_inbound_relations(db: DbWrapper, target_names: set,
                            module_ids: set) -> list:
    """返回所有指向待删模块的引用(其它模块的 relation 字段 + 写回规则)。

    relation 目标两种存法:显式 ``field_config->relation->>target_module``,
    或按 ``<target>_id`` 命名约定。两者都查;待删模块自身(含子明细)排除。
    再查 field_writeback 是否有规则牵涉待删模块。返回 "module.field" 列表。
    """
    names = list(target_names)
    id_cols = [f"{n}_id" for n in names]
    rel = db.fetchall(
        """
        SELECT c.name || '.' || mf.field_name AS ref
        FROM module_fields mf JOIN collections c ON c.id = mf.module_id
        WHERE mf.field_type = 'relation'
          AND c.name <> ALL(%(names)s)
          AND ( mf.field_config->'relation'->>'target_module' = ANY(%(names)s)
                OR mf.field_name = ANY(%(idcols)s) )
        """,
        {"names": names, "idcols": id_cols},
    )
    refs = [r["ref"] for r in (rel or [])]
    if module_ids:
        wb = db.fetchall(
            "SELECT id FROM field_writeback "
            "WHERE source_module_id = ANY(%(ids)s) OR target_module_id = ANY(%(ids)s)",
            {"ids": list(module_ids)},
        )
        refs += [f"writeback#{r['id']}" for r in (wb or [])]
    return refs


def _purge_stock_residue(db: DbWrapper, names: list) -> None:
    """清掉 revert 的 stock 功能残留(无 FK,不会被 CASCADE 带走)。表不在就跳过。"""
    for tbl in _RESIDUE_TABLES:
        if _table_exists(db, tbl):
            db.execute(f"DELETE FROM {tbl} WHERE source_module = ANY(%s)", (names,))


def _snapshot_one(db: DbWrapper, mname: str, mid: int) -> dict:
    """单个模块(父或子)的完整快照:collections 行 + 字段定义 + 全部数据行。"""
    _assert_safe_ident(mname)
    coll = db.fetchone("SELECT * FROM collections WHERE id = %s", (mid,))
    fields = db.fetchall(
        "SELECT * FROM module_fields WHERE module_id = %s ORDER BY sort_order", (mid,))
    rows = db.fetchall(f'SELECT * FROM "{mname}"')
    return {"collection": coll, "fields": list(fields or []),
            "rows": list(rows or [])}


def _archive_module(db: DbWrapper, name: str, row: dict, subs: list,
                    actor: dict, reason: Optional[str]) -> int:
    """删前把整模块(含子明细 + stock 残留)快照进 module_archive,返回归档 id。"""
    module_id = int(row["id"])
    payload = _snapshot_one(db, name, module_id)
    payload["subdetails"] = [
        {**_snapshot_one(db, s["name"], int(s["id"])),
         "detail_kind": s.get("detail_kind")}
        for s in subs
    ]
    all_names = [name] + [s["name"] for s in subs]
    stock = {}
    for tbl in _RESIDUE_TABLES:
        if _table_exists(db, tbl):
            stock[tbl] = list(db.fetchall(
                f"SELECT * FROM {tbl} WHERE source_module = ANY(%s)", (all_names,)) or [])
    payload["stock"] = stock

    db.execute(
        "INSERT INTO module_archive "
        "(module_name, title, title_en, actor_id, actor_label, reason, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
        (name, row.get("title"), row.get("title_en"),
         int(actor["id"]), actor.get("username"), reason,
         json.dumps(payload, default=str)),
    )
    arc = db.fetchone(
        "SELECT id FROM module_archive WHERE module_name = %s "
        "ORDER BY id DESC LIMIT 1", (name,))
    return int(arc["id"])


def deprovision_module(db: DbWrapper, name: str, actor: dict,
                       reason: Optional[str], *, archive: bool = True) -> dict:
    """安全删除一个顶层自定义模块(含其子明细),原子。

    删前:核心模块保护 + 子明细不可单删 + 入向引用检查。删时(一个事务):
    (可选)整模块快照归档 → 清 stock 残留 → 先删子后删父 collections 行
    (CASCADE 带走 fields/权限/changelog/stock_effects) → DROP 物理表
    (CASCADE 带走 v_dashboard_* 视图)。返回删除摘要 + 归档 id。
    """
    _assert_safe_ident(name)
    row = db.fetchone(
        "SELECT id, title, title_en, parent_module_id FROM collections "
        "WHERE name = %s", (name,))
    if not row:
        raise HTTPException(status_code=404, detail=f"unknown module: {name!r}")
    if row.get("parent_module_id") is not None:
        raise HTTPException(
            status_code=400,
            detail=f"{name!r} is a subdetail; delete its parent module instead")
    if name in PROTECTED_MODULES:
        raise HTTPException(
            status_code=403, detail=f"core module {name!r} cannot be deleted")
    module_id = int(row["id"])

    subs = list(db.fetchall(
        "SELECT id, name, detail_kind FROM collections WHERE parent_module_id = %s",
        (module_id,)) or [])
    for s in subs:
        _assert_safe_ident(s["name"])

    target_names = {name} | {s["name"] for s in subs}
    module_ids = {module_id} | {int(s["id"]) for s in subs}
    refs = _find_inbound_relations(db, target_names, module_ids)
    if refs:
        raise HTTPException(
            status_code=409,
            detail=f"module {name!r} is referenced by: {', '.join(refs)}")

    with db.transaction():
        archive_id = (_archive_module(db, name, row, subs, actor, reason)
                      if archive else None)
        _purge_stock_residue(db, [name] + [s["name"] for s in subs])
        # 先删子明细 collections 行(parent_module_id RESTRICT),再删父。
        for s in subs:
            db.execute("DELETE FROM collections WHERE id = %s", (int(s["id"]),))
        db.execute("DELETE FROM collections WHERE id = %s", (module_id,))
        # DROP 物理表:子表先、父表后;CASCADE 带走依赖的 v_dashboard_* 视图。
        for s in subs:
            db.execute(f'DROP TABLE IF EXISTS "{s["name"]}" CASCADE')
        db.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')

    return {"name": name, "module_id": module_id,
            "subdetails": [s["name"] for s in subs], "archive_id": archive_id}


def _rebuild_module(db: DbWrapper, payload: dict, actor: dict,
                    reason: Optional[str]) -> None:
    """从快照重建模块:建表 + 注册 collections + 字段(系统元数据/自定义列)+ 灌回数据。"""
    def _rebuild_one(snap: dict, parent_id: Optional[int],
                     detail_kind: Optional[str]) -> int:
        coll = snap["collection"]
        nm = coll["name"]
        _assert_safe_ident(nm)
        if parent_id is None:
            _create_record_table(db, nm)
        else:
            parent_name = db.fetchone(
                "SELECT name FROM collections WHERE id = %s", (parent_id,))["name"]
            _create_subdetail_table(db, nm, parent_name)
        db.execute(
            "INSERT INTO collections (name, title, title_en, parent_module_id, "
            "detail_kind, kind, description, headline_fields, list_fields, "
            "segments_config, metadata) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)",
            (nm, coll.get("title") or nm, coll.get("title_en"), parent_id,
             detail_kind, coll.get("kind") or "crm", coll.get("description"),
             json.dumps(coll.get("headline_fields")),
             json.dumps(coll.get("list_fields")),
             json.dumps(coll.get("segments_config")),
             json.dumps(coll.get("metadata") or {})),
        )
        new_id = int(db.fetchone(
            "SELECT id FROM collections WHERE name = %s", (nm,))["id"])
        mf = ModuleFieldsDb(db)
        module = {"id": new_id, "name": nm, "table_name": nm}
        for f in snap["fields"]:
            if f.get("is_system"):
                # 系统列已由建表创建,只补 module_fields 元数据。
                mf.insert_field({
                    "module_id": new_id, "field_name": f["field_name"],
                    "field_label": f.get("field_label"), "field_type": f["field_type"],
                    "is_required": bool(f.get("is_required")), "is_system": True,
                    "is_readonly": bool(f.get("is_readonly")),
                    "is_searchable": bool(f.get("is_searchable")),
                    "sort_order": int(f.get("sort_order") or 0),
                    "options": json.dumps(f["options"]) if f.get("options") else None,
                    "default_value": f.get("default_value"),
                    "description": f.get("description"),
                    "field_config": json.dumps(f.get("field_config") or {}),
                })
            else:
                # 自定义字段:provision_field 既加物理列又写元数据。
                provision_field(db, module, {
                    "field_name": f["field_name"], "field_label": f.get("field_label"),
                    "field_type": f["field_type"], "is_required": f.get("is_required"),
                    "options": f.get("options"), "default_value": f.get("default_value"),
                    "description": f.get("description"),
                    "field_config": f.get("field_config"),
                }, actor, reason)
        # 灌回数据行(列名取自行 dict 的键,值原样回插)。
        for r in snap["rows"]:
            cols = list(r.keys())
            if not cols:
                continue
            collist = ", ".join(f'"{c}"' for c in cols)
            ph = ", ".join(["%s"] * len(cols))
            db.execute(f'INSERT INTO "{nm}" ({collist}) VALUES ({ph})',
                       tuple(r[c] for c in cols))
        return new_id

    parent_id = _rebuild_one(payload, None, None)
    _grant_default_perms(db, parent_id)
    for sub in payload.get("subdetails") or []:
        _rebuild_one(sub, parent_id, sub.get("detail_kind"))
    # stock 残留回灌(若有)
    for tbl, rows in (payload.get("stock") or {}).items():
        for r in rows:
            cols = list(r.keys())
            if not cols:
                continue
            collist = ", ".join(f'"{c}"' for c in cols)
            ph = ", ".join(["%s"] * len(cols))
            db.execute(f'INSERT INTO {tbl} ({collist}) VALUES ({ph})',
                       tuple(r[c] for c in cols))


def restore_module(db: DbWrapper, archive_id: int, actor: dict,
                   reason: Optional[str]) -> dict:
    """从归档还原一个已删模块。名字若被重新占用则拒绝。原子。"""
    arc = db.fetchone(
        "SELECT id, module_name, title, title_en, payload FROM module_archive "
        "WHERE id = %s AND restored_at IS NULL", (archive_id,))
    if not arc:
        raise HTTPException(
            status_code=404,
            detail=f"archive {archive_id} not found or already restored")
    name = arc["module_name"]
    payload = arc["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    _validate_module_name(db, name)  # 名字没被重新占用才能还原
    with db.transaction():
        _rebuild_module(db, payload, actor, reason)
        db.execute("UPDATE module_archive SET restored_at = now() WHERE id = %s",
                   (archive_id,))
    return {"name": name, "archive_id": int(arc["id"])}


__all__ = ["provision_module", "rename_module", "provision_subdetail",
           "deprovision_module", "restore_module",
           "_validate_module_name", "_create_record_table"]
