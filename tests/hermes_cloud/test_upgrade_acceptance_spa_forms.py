"""Upgrade-acceptance — SPA 记录表单 ↔ schema 字段类型契约。

2026-07 事故:alembic 0107 把 module_fields 的 select/radio 归一成 picklist
之后,CreateRecordModal 手搓的 ``ft === "select"`` 判断认不出新标签,建档
表单所有下拉框(状态/行业/…)静默塌成纯文本输入——dev 和 prod 同时坏,
且没有任何测试报警。这一类"类型标签演进,渲染器没跟上"的漂移从此在这里
卡死:

  * 后端 canonical 类型 + legacy 别名(field_provisioning 是 source of
    truth)必须全部被前端 fieldKinds.ts 的选择类集合覆盖;
  * 记录表单渲染器必须经由 fieldKinds 谓词分支,不许再手搓类型元组;
  * hermes 侧 list/detail 本地化必须走 picklist.is_select_like。

hermes 升级 / schema 迁移合并前必跑(与其余 test_upgrade_acceptance_* 一起)。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIELD_KINDS_TS = REPO / "frontend" / "src" / "lib" / "fieldKinds.ts"
CREATE_MODAL_TSX = REPO / "frontend" / "src" / "components" / "CreateRecordModal.tsx"
ALLINFO_TSX = REPO / "frontend" / "src" / "components" / "AllInfoDrawer.tsx"


def _ts_set_literal(src: str, name: str) -> set[str]:
    """Parse ``const NAME = new Set(["a", "b"])`` from fieldKinds.ts."""
    m = re.search(rf"{name}\s*=\s*new Set\(\[(.*?)\]\)", src, re.S)
    assert m, f"fieldKinds.ts lost the {name} set"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _spa_select_like_types() -> set[str]:
    src = FIELD_KINDS_TS.read_text(encoding="utf-8")
    return _ts_set_literal(src, "PICKLIST_TYPES") | _ts_set_literal(
        src, "MULTISELECT_TYPES"
    )


# ── 契约 1:后端类型全集 ⊆ 前端渲染器认识的集合 ─────────────────────────────

def test_spa_covers_canonical_option_backed_types():
    """canonical picklist/multiselect(field_provisioning SQL_TYPE_MAP 里
    仅有的两个选项背书类型)必须能在 SPA 渲染成选项控件。"""
    spa = _spa_select_like_types()
    for canonical in ("picklist", "multiselect"):
        assert canonical in spa, (
            f"SPA fieldKinds.ts 不认 canonical 类型 {canonical!r} — "
            f"建档/编辑表单会把它渲染成纯文本框(0107 事故复发)"
        )


def test_spa_covers_legacy_aliases():
    """FIELD_TYPE_ALIASES 的每个 legacy 键(select/radio/multipicklist)
    也必须被 SPA 认识——未迁移租户/旧 payload 还会带这些标签。"""
    from hermes_cloud.app.crm.field_provisioning import FIELD_TYPE_ALIASES

    spa = _spa_select_like_types()
    for legacy in FIELD_TYPE_ALIASES:
        assert legacy in spa, (
            f"SPA fieldKinds.ts 不认 legacy 类型 {legacy!r}"
        )


def test_backend_is_select_like_agrees_with_spa():
    """前后端两份选择类集合必须一致——单边加类型就是下一次事故。"""
    from hermes.crm.picklist import SELECT_LIKE_FIELD_TYPES

    assert _spa_select_like_types() == set(SELECT_LIKE_FIELD_TYPES)


# ── 契约 2:渲染器经由共享谓词分支,不许手搓类型元组 ─────────────────────────

def test_create_modal_uses_field_kinds():
    src = CREATE_MODAL_TSX.read_text(encoding="utf-8")
    assert '@/lib/fieldKinds' in src, "CreateRecordModal 不再走 fieldKinds 谓词"
    assert 'ft === "select"' not in src, (
        "CreateRecordModal 又出现手搓 select 判断——必须用 isPicklistLike"
    )


def test_allinfo_drawer_uses_field_kinds():
    src = ALLINFO_TSX.read_text(encoding="utf-8")
    assert '@/lib/fieldKinds' in src, "AllInfoDrawer 不再走 fieldKinds 谓词"
    assert 'ft === "select"' not in src, (
        "AllInfoDrawer 又出现手搓 select 判断——必须用 isPicklistLike/isSelectLike"
    )


# ── 契约 3:hermes 服务端本地化走 is_select_like(patch 10 内容防升级冲飞) ──

def test_hermes_queries_use_is_select_like():
    import hermes.crm.detail_query as dq
    import hermes.crm.list_query as lq

    for mod in (lq, dq):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "is_select_like" in src, (
            f"{mod.__name__} 丢了 is_select_like——服务端选项本地化白名单退化"
        )
        assert '("select", "multiSelect")' not in src, (
            f"{mod.__name__} 又出现硬编码类型元组"
        )
