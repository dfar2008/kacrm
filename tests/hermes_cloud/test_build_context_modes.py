"""
测试 build_fill_context M1-T4 扩展：
- prompt 变量被渲染（own 字段替换）
- needs_graph 模式注入 semantic + subgraph（有 memory_entity_id 时）
- prompt 模式（needs_graph=False）不注入图谱
- 无 memory_entity_id 时不查子图、不报错
- search_memories 抛异常时被吞、fill 不炸
"""
from __future__ import annotations

import pytest

# ── 公共 fixture ──────────────────────────────────────────────────────────────

def _field_meta(mode: str = "prompt", mission: str = "", declared_fields=None):
    return {
        "field_name": "icp_level",
        "field_type": "picklist" if mode == "classify" else "text",
        "field_config": {
            "ai_fill": {
                "mode": mode,
                "mission": mission,
                "output_spec": "输出一个选项",
                "enabled": True,
                "inputs": {
                    "fields": declared_fields or [],
                    "interactions": {"enabled": False},
                },
            }
        },
    }


class _FakeDb:
    """最简 fake db——不支持任何查询（build_fill_context 不会走 interactions 分支）。"""
    def fetchall(self, *args, **kwargs):
        return []

    def fetchone(self, *args, **kwargs):
        return None


# ── 测试 1：own 字段变量被正确渲染 ───────────────────────────────────────────

def test_own_variable_rendered():
    """mission 中的 {name} 应被替换成 record["name"] 的值。"""
    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    rec = {"accounts_id": 1, "name": "华为技术", "industry": "科技"}
    fm = _field_meta(mode="prompt", mission="请判断 {name} 的 ICP 级别")
    ctx, basis = build_fill_context(db=None, module="accounts", record=rec, field_meta=fm)

    assert ctx.get("rendered_prompt") == "请判断 华为技术 的 ICP 级别"
    assert "field:name" in basis


def test_own_variable_missing_becomes_placeholder():
    """record 中没有的变量应替换为 '(空)'。"""
    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    rec = {"accounts_id": 1, "industry": "科技"}
    fm = _field_meta(mode="prompt", mission="公司 {name} 行业 {industry}")
    ctx, basis = build_fill_context(db=None, module="accounts", record=rec, field_meta=fm)

    assert "(空)" in ctx["rendered_prompt"]
    assert "华为" not in ctx["rendered_prompt"]
    assert "field:industry" in basis
    # name 解析失败，不进 basis
    assert "field:name" not in basis


# ── 测试 2：classify 模式（needs_graph=True）注入 semantic + subgraph ──────────

def test_classify_mode_injects_graph_with_entity(monkeypatch):
    """classify 模式 + memory_entity_id 存在 → 注入 semantic 和 subgraph。"""
    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    fake_rows = [{"id": 101, "content": "华为是一家大型科技公司", "similarity": 0.9}]
    fake_graph = {
        "entities": [{"id": 42, "name": "华为"}],
        "edges": [{"src_entity_id": 42, "dst_entity_id": 99, "relation": "合作"}],
    }

    monkeypatch.setattr(
        "hermes_cloud.app.crm.ai_attributes.search_memories",
        lambda **kw: fake_rows,
        raising=False,
    )
    monkeypatch.setattr(
        "hermes_cloud.app.crm.ai_attributes.subgraph",
        lambda **kw: fake_graph,
        raising=False,
    )

    # 直接 patch import 路径（build_fill_context 用 lazy import）
    import hermes_cloud.app.crm.ai_attributes as _mod
    import types

    _fake_vs = types.ModuleType("hermes.memory.vector_store")
    _fake_vs.search_memories = lambda **kw: fake_rows
    _fake_ent = types.ModuleType("hermes.memory.entities")
    _fake_ent.subgraph = lambda **kw: fake_graph

    monkeypatch.setitem(
        __import__("sys").modules, "hermes.memory.vector_store", _fake_vs
    )
    monkeypatch.setitem(
        __import__("sys").modules, "hermes.memory.entities", _fake_ent
    )

    rec = {
        "accounts_id": 1,
        "name": "华为技术",
        "industry": "科技",
        "memory_entity_id": 42,
    }
    fm = _field_meta(mode="classify", mission="判断 {name} 的客户等级")
    db = _FakeDb()

    ctx, basis = build_fill_context(db=db, module="accounts", record=rec, field_meta=fm, user_id=1)

    assert "semantic" in ctx, "classify 模式应有 semantic"
    assert len(ctx["semantic"]) == 1
    assert ctx["semantic"][0]["id"] == 101

    assert "subgraph" in ctx, "有 memory_entity_id 时应有 subgraph"
    assert ctx["subgraph"]["entities"][0]["name"] == "华为"

    assert any(b.startswith("semantic:") for b in basis)
    assert "graph:entity:42" in basis


# ── 测试 3：summarize 模式也需要图谱 ─────────────────────────────────────────

def test_summarize_mode_needs_graph(monkeypatch):
    """summarize 同属 needs_graph=True 模式，应触发 search_memories。"""
    import sys, types

    fake_rows = [{"id": 200, "content": "摘要内容", "similarity": 0.8}]
    _fake_vs = types.ModuleType("hermes.memory.vector_store")
    _fake_vs.search_memories = lambda **kw: fake_rows
    _fake_ent = types.ModuleType("hermes.memory.entities")
    _fake_ent.subgraph = lambda **kw: {"entities": [], "edges": []}
    monkeypatch.setitem(sys.modules, "hermes.memory.vector_store", _fake_vs)
    monkeypatch.setitem(sys.modules, "hermes.memory.entities", _fake_ent)

    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    rec = {"contacts_id": 5, "name": "张三", "company": "阿里巴巴"}
    fm = _field_meta(mode="summarize", mission="总结 {name} 的情况")
    ctx, basis = build_fill_context(db=_FakeDb(), module="contacts", record=rec, field_meta=fm)

    assert "semantic" in ctx
    assert any(b.startswith("semantic:") for b in basis)


# ── 测试 4：prompt 模式（needs_graph=False）不注入图谱 ───────────────────────

def test_prompt_mode_no_graph(monkeypatch):
    """prompt 模式下不应查询 search_memories 或 subgraph。"""
    import sys, types

    call_log = []

    _fake_vs = types.ModuleType("hermes.memory.vector_store")
    _fake_vs.search_memories = lambda **kw: (call_log.append("search"), [])[-1]
    _fake_ent = types.ModuleType("hermes.memory.entities")
    _fake_ent.subgraph = lambda **kw: (call_log.append("subgraph"), {})[-1]
    monkeypatch.setitem(sys.modules, "hermes.memory.vector_store", _fake_vs)
    monkeypatch.setitem(sys.modules, "hermes.memory.entities", _fake_ent)

    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    rec = {"accounts_id": 1, "name": "测试公司", "memory_entity_id": 99}
    fm = _field_meta(mode="prompt", mission="分析 {name}")
    ctx, basis = build_fill_context(db=_FakeDb(), module="accounts", record=rec, field_meta=fm)

    assert "semantic" not in ctx, "prompt 模式不应注入 semantic"
    assert "subgraph" not in ctx, "prompt 模式不应注入 subgraph"
    assert not any(b.startswith("semantic:") for b in basis)
    assert not any(b.startswith("graph:") for b in basis)
    # search_memories 和 subgraph 都不应被调用
    assert "search" not in call_log
    assert "subgraph" not in call_log


# ── 测试 5：无 memory_entity_id 时不查子图、不报错 ───────────────────────────

def test_no_entity_id_skips_subgraph(monkeypatch):
    """record 没有 memory_entity_id 时，subgraph 不被调用，也不报错。"""
    import sys, types

    subgraph_called = []

    _fake_vs = types.ModuleType("hermes.memory.vector_store")
    _fake_vs.search_memories = lambda **kw: [{"id": 301, "content": "x", "similarity": 0.7}]
    _fake_ent = types.ModuleType("hermes.memory.entities")
    _fake_ent.subgraph = lambda **kw: subgraph_called.append("called") or {}
    monkeypatch.setitem(sys.modules, "hermes.memory.vector_store", _fake_vs)
    monkeypatch.setitem(sys.modules, "hermes.memory.entities", _fake_ent)

    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    # 故意不放 memory_entity_id
    rec = {"accounts_id": 2, "name": "小公司"}
    fm = _field_meta(mode="classify", mission="判断 {name} 等级")
    ctx, basis = build_fill_context(db=_FakeDb(), module="accounts", record=rec, field_meta=fm)

    assert "semantic" in ctx, "仍应有 semantic 检索"
    assert "subgraph" not in ctx, "无 entity_id → 不应有 subgraph"
    assert not subgraph_called, "subgraph 函数不应被调用"
    assert not any(b.startswith("graph:") for b in basis)


# ── 测试 6：search_memories 抛异常时被吞 ─────────────────────────────────────

def test_search_memories_exception_swallowed(monkeypatch):
    """search_memories 抛异常时 build_fill_context 不应向外传播异常。"""
    import sys, types

    _fake_vs = types.ModuleType("hermes.memory.vector_store")
    _fake_vs.search_memories = lambda **kw: (_ for _ in ()).throw(RuntimeError("向量服务挂了"))
    _fake_ent = types.ModuleType("hermes.memory.entities")
    _fake_ent.subgraph = lambda **kw: {"entities": [], "edges": []}
    monkeypatch.setitem(sys.modules, "hermes.memory.vector_store", _fake_vs)
    monkeypatch.setitem(sys.modules, "hermes.memory.entities", _fake_ent)

    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    rec = {"accounts_id": 3, "name": "崩溃公司", "memory_entity_id": 77}
    fm = _field_meta(mode="classify", mission="判断 {name}")

    # 不应抛出异常
    ctx, basis = build_fill_context(db=_FakeDb(), module="accounts", record=rec, field_meta=fm)

    # semantic 可能不在（因为异常被吞），但函数正常返回
    assert isinstance(ctx, dict)
    assert isinstance(basis, list)
    assert "fields" in ctx


# ── 测试 7：向后兼容——无 mode 默认 prompt ─────────────────────────────────────

def test_no_mode_defaults_to_prompt_no_graph():
    """旧配置（无 mode 字段）应默认 prompt 模式，不注入图谱。"""
    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    # 旧式 field_meta，没有 mode 字段
    fm = {
        "field_name": "summary",
        "field_type": "text",
        "field_config": {
            "ai_fill": {
                "mission": "写摘要",
                "output_spec": "100字以内",
                "enabled": True,
                "inputs": {"fields": [], "interactions": {"enabled": False}},
            }
        },
    }
    rec = {"contacts_id": 9, "name": "老客户", "memory_entity_id": 55}
    ctx, basis = build_fill_context(db=_FakeDb(), module="contacts", record=rec, field_meta=fm)

    assert "semantic" not in ctx
    assert "subgraph" not in ctx
    # fields + interactions 照旧保留
    assert "fields" in ctx
    assert "interactions" in ctx


# ── 测试 8：db=None 时跳过图谱注入（不崩溃） ─────────────────────────────────

def test_db_none_skips_graph():
    """db=None 时，即使是 classify 模式也不尝试查询图谱。"""
    from hermes_cloud.app.crm.ai_attributes import build_fill_context

    rec = {"accounts_id": 1, "name": "测试", "memory_entity_id": 10}
    fm = _field_meta(mode="classify", mission="判断 {name}")
    ctx, basis = build_fill_context(db=None, module="accounts", record=rec, field_meta=fm)

    assert "semantic" not in ctx
    assert "subgraph" not in ctx
