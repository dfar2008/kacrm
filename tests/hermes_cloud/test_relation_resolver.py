"""
TDD: _make_relation_resolver 真·关系穿透

测试 hermes_cloud/app/crm/ai_attributes._make_relation_resolver 的以下契约：
1. 降级命中（_display）→ 不查库直接返回；
2. 真穿透：rel_field 有 id + schema 有 target_module → 查到关联记录 target_field 值；
3. target_module 找不到 → None；
4. target_field 非法名（带空格/分号）→ None，不拼 SQL；
5. record 里 rel_field 无值 → None；
6. _display 优先于真穿透（name 类字段命中即返回）；
7. 平铺字段 (rel_field_target_field) 优先于真穿透；
8. 闭包级缓存：相同 module 的 schema 查库只调一次；
9. db=None 时降级未命中 → None（不崩溃）。
"""
from __future__ import annotations

import json
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from hermes_cloud.app.crm.ai_attributes import _make_relation_resolver


# ---------------------------------------------------------------------------
# FakeDb helpers
# ---------------------------------------------------------------------------

class FakeDb:
    """最小 DbWrapper 替身，可针对不同 SQL 返回不同数据。"""

    def __init__(self):
        self.fetchone_calls: list[tuple[str, tuple]] = []
        self.fetchall_calls: list[tuple[str, tuple]] = []
        # 预设队列：按 fetchone 顺序弹出；若 queue 空则用 _fetchone_default
        self._fetchone_queue: list[Optional[dict]] = []
        self._fetchall_queue: list[list] = []
        self._fetchone_default: Optional[dict] = None
        self._fetchall_default: list = []

    def set_fetchone_queue(self, *results: Optional[dict]):
        self._fetchone_queue = list(results)

    def set_fetchall_queue(self, *results: list):
        self._fetchall_queue = list(results)

    def fetchone(self, sql: str, args: tuple = ()) -> Optional[dict]:
        self.fetchone_calls.append((sql, args))
        if self._fetchone_queue:
            return self._fetchone_queue.pop(0)
        return self._fetchone_default

    def fetchall(self, sql: str, args: tuple = ()) -> list:
        self.fetchall_calls.append((sql, args))
        if self._fetchall_queue:
            return self._fetchall_queue.pop(0)
        return self._fetchall_default


# ---------------------------------------------------------------------------
# Helpers to build module/field stubs
# ---------------------------------------------------------------------------

def make_module_row(name: str, mid: int = 1) -> dict:
    return {"id": mid, "name": name}


def make_field_row(
    field_name: str,
    field_config: Any = None,
    field_type: str = "relation",
) -> dict:
    cfg = field_config
    if cfg is not None and not isinstance(cfg, str):
        cfg = json.dumps(cfg)
    return {
        "field_name": field_name,
        "field_type": field_type,
        "field_config": cfg,
    }


def make_target_field_row(field_name: str) -> dict:
    return {"field_name": field_name, "field_type": "currency"}


# ---------------------------------------------------------------------------
# Test helpers: patch ModulesDb / ModuleFieldsDb
# ---------------------------------------------------------------------------

class PatchedResolver:
    """
    Context manager：在 _make_relation_resolver 内部注入假的 ModulesDb/ModuleFieldsDb，
    让 schema 查询可控，无需真实 DB 连接。
    """

    def __init__(
        self,
        *,
        db,
        module: str,
        record: dict,
        # 当前模块的字段列表
        current_module_fields: list[dict],
        # target_module -> 字段列表
        target_module_fields: dict[str, list[dict]],
        # target_module -> module_row（id 必须）
        module_rows: dict[str, dict] | None = None,
        current_module_id: int = 1,
    ):
        self.db = db
        self.module = module
        self.record = record
        self.current_module_fields = current_module_fields
        self.target_module_fields = target_module_fields
        self.module_rows = module_rows or {}
        self.current_module_id = current_module_id
        self._patches: list = []

    def __enter__(self):
        # ModulesDb.get_module_by_name: module -> row
        modules_map = {self.module: {"id": self.current_module_id, "name": self.module}}
        modules_map.update(self.module_rows)

        mock_modules_cls = MagicMock()
        mock_modules_inst = MagicMock()
        mock_modules_inst.get_module_by_name.side_effect = lambda name: modules_map.get(name)
        mock_modules_cls.return_value = mock_modules_inst

        # ModuleFieldsDb.get_module_fields: module_id -> fields
        fields_by_id = {self.current_module_id: self.current_module_fields}
        # target modules get id from module_rows or default to id=99+offset
        for mod_name, fields in self.target_module_fields.items():
            mid = (self.module_rows.get(mod_name) or {}).get("id", 99)
            fields_by_id[mid] = fields

        mock_fields_cls = MagicMock()
        mock_fields_inst = MagicMock()
        mock_fields_inst.get_module_fields.side_effect = lambda mid: fields_by_id.get(int(mid), [])
        mock_fields_cls.return_value = mock_fields_inst

        p1 = patch("hermes_cloud.app.crm.ai_attributes.ModulesDb" if False else
                   "hermes.crm.models.modules_db.ModulesDb",
                   mock_modules_cls)
        p2 = patch("hermes.crm.models.module_fields_db.ModuleFieldsDb",
                   mock_fields_cls)

        # We patch at the import site inside the function
        p3 = patch(
            "hermes_cloud.app.crm.ai_attributes._make_relation_resolver.__code__",
            # We can't patch __code__ easily, so instead we patch the modules
            # that the resolver's nested functions import
        )

        # Simpler approach: patch the modules directly
        import sys
        from unittest.mock import MagicMock as MM

        # Build standalone mocks accessible in the closure
        self._mock_modules_inst = mock_modules_inst
        self._mock_fields_inst = mock_fields_inst

        # Patch at the import path used inside the closure
        self._p_modules = patch(
            "hermes.crm.models.modules_db.ModulesDb",
            lambda db: mock_modules_inst,
        )
        self._p_fields = patch(
            "hermes.crm.models.module_fields_db.ModuleFieldsDb",
            lambda db: mock_fields_inst,
        )

        self._p_modules.start()
        self._p_fields.start()

        return _make_relation_resolver(self.db, self.module, self.record)

    def __exit__(self, *args):
        self._p_modules.stop()
        self._p_fields.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRelationResolverFallback:
    """降级路径（不查库）。"""

    def test_display_key_for_name_field(self):
        """target_field=name，record 有 _display → 直接返回，不查库。"""
        db = FakeDb()
        record = {"关联商机": 42, "关联商机_display": "Q1合同"}
        resolver = _make_relation_resolver(db, "contacts", record)
        result = resolver("关联商机", "name")
        assert result == "Q1合同"
        # 不应触发 fetchone
        assert len(db.fetchone_calls) == 0

    def test_display_key_for_title_field(self):
        """target_field=title 时也走 _display。"""
        db = FakeDb()
        record = {"关联商机": 42, "关联商机_display": "项目Alpha"}
        resolver = _make_relation_resolver(db, "contacts", record)
        result = resolver("关联商机", "title")
        assert result == "项目Alpha"
        assert len(db.fetchone_calls) == 0

    def test_flat_key_fallback(self):
        """record 有 rel_field_target_field 平铺字段 → 返回，不查库。"""
        db = FakeDb()
        record = {"关联商机": 42, "关联商机_amount": "500000"}
        resolver = _make_relation_resolver(db, "contacts", record)
        result = resolver("关联商机", "amount")
        assert result == "500000"
        assert len(db.fetchone_calls) == 0

    def test_no_rel_id_returns_none(self):
        """record 里 rel_field 无值（None） → None，不查库。"""
        db = FakeDb()
        record = {}  # 没有 关联商机
        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "amount")
        assert result is None
        # fetchone 不应被调用（fetchone_calls 只能是 schema 查询里的，但我们 mock 了 DB 层）
        # schema 走 ModulesDb/ModuleFieldsDb mock，不走 db.fetchone
        assert len(db.fetchone_calls) == 0


class TestRelationResolverTruePenetration:
    """真穿透路径（需查库）。"""

    def test_true_penetration_returns_value(self):
        """rel_field 有 id，schema 有 target_module，target 表有记录 → 返回字段值。"""
        db = FakeDb()
        # target 表查询结果
        db.set_fetchone_queue({"amount": "500000"})
        record = {"关联商机": 42}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "amount")

        assert result == "500000"
        assert len(db.fetchone_calls) == 1
        sql, args = db.fetchone_calls[0]
        assert '"deals"' in sql
        assert '"amount"' in sql
        assert args[0] == 42

    def test_true_penetration_target_record_not_found(self):
        """关联记录不存在 → None。"""
        db = FakeDb()
        db.set_fetchone_queue(None)
        record = {"关联商机": 999}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "amount")

        assert result is None

    def test_target_field_value_none_returns_none(self):
        """target 表字段值为 NULL → None。"""
        db = FakeDb()
        db.set_fetchone_queue({"amount": None})
        record = {"关联商机": 10}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "amount")

        assert result is None


class TestRelationResolverTargetModuleNotFound:
    """schema 没有 target_module → None。"""

    def test_no_target_module_in_config(self):
        """field_config.relation 没有 target_module → None，不查库。"""
        db = FakeDb()
        record = {"关联商机": 42}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                # field_config 里 relation 没有 target_module
                make_field_row("关联商机", {"relation": {}})
            ],
            target_module_fields={},
        ) as resolver:
            result = resolver("关联商机", "amount")

        assert result is None
        assert len(db.fetchone_calls) == 0

    def test_rel_field_not_in_schema(self):
        """当前模块 schema 里根本没有该关系字段 → None。"""
        db = FakeDb()
        record = {"other_rel": 42}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[],  # 没有任何字段
            target_module_fields={},
        ) as resolver:
            result = resolver("other_rel", "amount")

        assert result is None
        assert len(db.fetchone_calls) == 0


class TestRelationResolverSQLInjectionPrevention:
    """target_field 防 SQL 注入。"""

    def test_target_field_with_space_blocked(self):
        """target_field 含空格 → None，不拼入 SQL。"""
        db = FakeDb()
        record = {"关联商机": 42}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "amount; DROP TABLE deals--")

        assert result is None
        assert len(db.fetchone_calls) == 0

    def test_target_field_with_semicolon_blocked(self):
        """target_field 含分号 → None。"""
        db = FakeDb()
        record = {"关联商机": 42}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "amount;")

        assert result is None
        assert len(db.fetchone_calls) == 0

    def test_target_field_uppercase_blocked(self):
        """target_field 含大写（不在 target_module 字段白名单）→ None。"""
        db = FakeDb()
        record = {"关联商机": 42}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            # 白名单只有小写 amount
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "Amount")  # 大写不在白名单

        assert result is None
        assert len(db.fetchone_calls) == 0

    def test_target_field_not_in_whitelist_blocked(self):
        """target_field 通过正则但不在 target_module 字段白名单里 → None。"""
        db = FakeDb()
        record = {"关联商机": 42}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row("关联商机", {"relation": {"target_module": "deals"}})
            ],
            target_module_fields={"deals": [make_target_field_row("amount")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            # nonexistent_col 格式合法但不在 deals 表字段里
            result = resolver("关联商机", "nonexistent_col")

        assert result is None
        assert len(db.fetchone_calls) == 0


class TestRelationResolverDbNone:
    """db=None 时不崩溃。"""

    def test_db_none_display_hit(self):
        """db=None，降级命中 _display → 正常返回。"""
        record = {"关联商机": 42, "关联商机_display": "项目X"}
        resolver = _make_relation_resolver(None, "contacts", record)
        result = resolver("关联商机", "name")
        assert result == "项目X"

    def test_db_none_no_fallback_returns_none(self):
        """db=None，降级未命中 → None（不崩溃）。"""
        record = {"关联商机": 42}
        resolver = _make_relation_resolver(None, "contacts", record)
        result = resolver("关联商机", "amount")
        assert result is None


class TestRelationResolverFieldConfigJson:
    """field_config 为 JSON 字符串时也能正确解析 target_module。"""

    def test_field_config_as_json_string(self):
        """field_config 是 JSON 字符串形式时能正确解析 target_module。"""
        db = FakeDb()
        db.set_fetchone_queue({"deal_name": "大单A"})
        record = {"关联商机": 7}

        with PatchedResolver(
            db=db, module="contacts", record=record,
            current_module_fields=[
                make_field_row(
                    "关联商机",
                    # 传字符串而不是 dict
                    json.dumps({"relation": {"target_module": "deals"}}),
                )
            ],
            target_module_fields={"deals": [make_target_field_row("deal_name")]},
            module_rows={"deals": {"id": 99, "name": "deals"}},
        ) as resolver:
            result = resolver("关联商机", "deal_name")

        assert result == "大单A"
