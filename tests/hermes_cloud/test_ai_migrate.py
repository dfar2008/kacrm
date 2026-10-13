"""TDD 单测 —— hermes_cloud/app/crm/ai_migrate.py

覆盖：
- infer_mode  （5 个核心场景）
- migrate_ai_fill（幂等 + ai_field 类型修正）
- run_migration runner（轻量冒烟，mock DB）
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch
import json
import pytest

from hermes_cloud.app.crm.ai_migrate import infer_mode, migrate_ai_fill, run_migration


# ─────────────────────────────────────────────────────────────────────────────
# infer_mode
# ─────────────────────────────────────────────────────────────────────────────

class TestInferMode:
    def _base(self, **kw) -> dict:
        """最小合法 ai_fill（无 mode）。"""
        base = {"enabled": True, "mission": "测试任务", "output_spec": "输出说明"}
        base.update(kw)
        return base

    # ── 规则 1：picklist/multiselect → classify ──────────────────────────────

    def test_picklist_no_mode_returns_classify(self):
        assert infer_mode("picklist", self._base()) == "classify"

    def test_multiselect_no_mode_returns_classify(self):
        assert infer_mode("multiselect", self._base()) == "classify"

    # ── 规则 2：联网关键词 → web_agent（仅限兼容 field_type）────────────────

    def test_text_with_web_keyword_in_mission_returns_web_agent(self):
        af = self._base(mission="查最近融资动态和估值")
        assert infer_mode("text", af) == "web_agent"

    def test_text_with_web_keyword_in_output_spec(self):
        af = self._base(output_spec="从官网抓取最新融资金额")
        assert infer_mode("text", af) == "web_agent"

    def test_number_with_zaixian_keyword(self):
        af = self._base(mission="联网获取最新员工人数")
        assert infer_mode("number", af) == "web_agent"

    def test_currency_with_zhaocha_keyword(self):
        af = self._base(mission="网上查该公司年营收")
        assert infer_mode("currency", af) == "web_agent"

    def test_text_with_inputs_web_returns_web_agent(self):
        af = self._base(inputs={"web": True, "fields": []})
        assert infer_mode("text", af) == "web_agent"

    # picklist 即使有关键词也是 classify（规则 1 优先）
    def test_picklist_with_web_keyword_still_classify(self):
        af = self._base(mission="查最近融资阶段")
        assert infer_mode("picklist", af) == "classify"

    # ── 规则 3：普通 text + 无联网迹象 → prompt ─────────────────────────────

    def test_text_plain_mission_returns_prompt(self):
        af = self._base(mission="根据已有字段总结公司简介")
        assert infer_mode("text", af) == "prompt"

    def test_number_plain_returns_prompt(self):
        af = self._base(mission="计算本季度成交金额占比")
        assert infer_mode("number", af) == "prompt"

    def test_textarea_returns_prompt(self):
        af = self._base(mission="汇总本季度跟进备注")
        assert infer_mode("textarea", af) == "prompt"

    def test_unknown_field_type_returns_prompt(self):
        af = self._base(mission="没有对应规则的字段类型")
        assert infer_mode("relation", af) == "prompt"

    def test_empty_mission_still_prompt(self):
        """空 mission 也能运行，不抛异常。"""
        af = {"enabled": True}
        assert infer_mode("text", af) == "prompt"


# ─────────────────────────────────────────────────────────────────────────────
# migrate_ai_fill
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrateAiFill:
    def _base(self, **kw) -> dict:
        base = {"enabled": True, "mission": "任务", "output_spec": "说明"}
        base.update(kw)
        return base

    # ── 补 mode ─────────────────────────────────────────────────────────────

    def test_picklist_without_mode_gets_classify(self):
        new_ft, new_af = migrate_ai_fill("picklist", self._base())
        assert new_ft == "picklist"
        assert new_af["mode"] == "classify"

    def test_text_web_keyword_gets_web_agent(self):
        af = self._base(mission="查最近融资")
        _, new_af = migrate_ai_fill("text", af)
        assert new_af["mode"] == "web_agent"

    def test_text_plain_gets_prompt(self):
        _, new_af = migrate_ai_fill("text", self._base(mission="总结字段"))
        assert new_af["mode"] == "prompt"

    # ── 幂等：已有合法 mode 且兼容 ──────────────────────────────────────────

    def test_existing_classify_on_picklist_unchanged(self):
        af = self._base(mode="classify")
        new_ft, new_af = migrate_ai_fill("picklist", af)
        assert new_ft == "picklist"
        assert new_af["mode"] == "classify"

    def test_existing_prompt_on_text_unchanged(self):
        af = self._base(mode="prompt")
        new_ft, new_af = migrate_ai_fill("text", af)
        assert new_ft == "text"
        assert new_af["mode"] == "prompt"

    def test_existing_web_agent_on_text_unchanged(self):
        af = self._base(mode="web_agent")
        new_ft, new_af = migrate_ai_fill("text", af)
        assert new_ft == "text"
        assert new_af["mode"] == "web_agent"

    def test_existing_summarize_on_textarea_unchanged(self):
        af = self._base(mode="summarize")
        new_ft, new_af = migrate_ai_fill("textarea", af)
        assert new_ft == "textarea"
        assert new_af["mode"] == "summarize"

    # 不兼容的旧 mode（如 classify on text）→ 重新推断
    def test_incompatible_mode_is_replaced(self):
        """旧 mode=classify 挂在 text 字段上（非法）→ 重新推断为 prompt。"""
        af = self._base(mode="classify")
        _, new_af = migrate_ai_fill("text", af)
        # text + 无联网关键词 → prompt
        assert new_af["mode"] == "prompt"

    # ── ai_field 类型修正 ────────────────────────────────────────────────────

    def test_ai_field_with_output_type_number_becomes_number(self):
        af = self._base(output_type="number", mission="总结员工数")
        new_ft, new_af = migrate_ai_fill("ai_field", af)
        assert new_ft == "number"
        assert "mode" in new_af

    def test_ai_field_with_output_type_text_becomes_text(self):
        af = self._base(output_type="text")
        new_ft, new_af = migrate_ai_fill("ai_field", af)
        assert new_ft == "text"

    def test_ai_field_with_output_type_picklist_becomes_picklist_classify(self):
        af = self._base(output_type="picklist")
        new_ft, new_af = migrate_ai_fill("ai_field", af)
        assert new_ft == "picklist"
        assert new_af["mode"] == "classify"

    def test_ai_field_no_output_type_defaults_to_text(self):
        af = self._base()  # no output_type
        new_ft, new_af = migrate_ai_fill("ai_field", af)
        assert new_ft == "text"
        assert "mode" in new_af

    def test_ai_field_with_boolean_output_type_falls_back_to_text(self):
        """boolean 不是合法 field_type，降级为 text。"""
        af = self._base(output_type="boolean")
        new_ft, _ = migrate_ai_fill("ai_field", af)
        assert new_ft == "text"

    # ── 不改原始 dict（浅拷贝安全性）───────────────────────────────────────

    def test_does_not_mutate_input(self):
        af = {"enabled": True, "mission": "任务", "output_spec": "说明"}
        original_keys = set(af.keys())
        migrate_ai_fill("picklist", af)
        assert set(af.keys()) == original_keys
        assert "mode" not in af


# ─────────────────────────────────────────────────────────────────────────────
# run_migration (runner 冒烟，mock DB)
# ─────────────────────────────────────────────────────────────────────────────

def _make_db(rows):
    """构造 mock DbWrapper，fetchall 返回指定行，execute 记录调用。"""
    db = MagicMock()
    db.fetchall.return_value = rows
    return db


class TestRunMigration:
    def _row(self, fid, ft, af_extra=None):
        af = {"enabled": True, "mission": "任务", "output_spec": "说明"}
        if af_extra:
            af.update(af_extra)
        return {
            "id": fid,
            "module_id": 1,
            "field_name": f"f_{fid}",
            "field_type": ft,
            "field_config": {"ai_fill": af},
        }

    def test_dry_run_returns_changes_without_update(self):
        db = _make_db([self._row(1, "picklist")])
        changes = run_migration(db, apply=False)
        assert len(changes) == 1
        assert changes[0]["new_mode"] == "classify"
        # dry-run 不应调用 execute
        db.execute.assert_not_called()

    def test_apply_true_calls_execute(self):
        db = _make_db([self._row(2, "picklist")])
        changes = run_migration(db, apply=True)
        assert len(changes) == 1
        db.execute.assert_called_once()
        sql, args = db.execute.call_args[0]
        assert "UPDATE module_fields" in sql
        assert args[2] == 2  # WHERE id=%s

    def test_no_changes_when_already_valid(self):
        """已有合法 mode=classify + picklist → 无变化。"""
        row = self._row(3, "picklist", af_extra={"mode": "classify"})
        db = _make_db([row])
        changes = run_migration(db, apply=False)
        assert changes == []

    def test_skips_rows_without_ai_fill(self):
        db = _make_db([
            {"id": 4, "module_id": 1, "field_name": "plain",
             "field_type": "text", "field_config": {"formula": "x+1"}},
        ])
        changes = run_migration(db, apply=False)
        assert changes == []

    def test_skips_disabled_ai_fill(self):
        row = self._row(5, "picklist", af_extra={"enabled": False})
        db = _make_db([row])
        changes = run_migration(db, apply=False)
        assert changes == []

    def test_skips_null_field_config(self):
        db = _make_db([
            {"id": 6, "module_id": 1, "field_name": "no_cfg",
             "field_type": "text", "field_config": None},
        ])
        changes = run_migration(db, apply=False)
        assert changes == []

    def test_field_config_as_json_string(self):
        """field_config 是 JSON 字符串（老 psycopg2 风格）也能处理。"""
        af = {"enabled": True, "mission": "任务", "output_spec": "说明"}
        row = {
            "id": 7, "module_id": 1, "field_name": "str_cfg",
            "field_type": "picklist",
            "field_config": json.dumps({"ai_fill": af}),
        }
        db = _make_db([row])
        changes = run_migration(db, apply=False)
        assert len(changes) == 1
        assert changes[0]["new_mode"] == "classify"

    def test_apply_updates_field_type_for_ai_field(self):
        """ai_field 类型在 apply 时被修正为 number。"""
        af = {"enabled": True, "mission": "任务", "output_spec": "说明",
              "output_type": "number"}
        row = {
            "id": 8, "module_id": 1, "field_name": "ai_f",
            "field_type": "ai_field",
            "field_config": {"ai_fill": af},
        }
        db = _make_db([row])
        changes = run_migration(db, apply=True)
        assert len(changes) == 1
        assert changes[0]["new_field_type"] == "number"
        db.execute.assert_called_once()
        _, args = db.execute.call_args[0], db.execute.call_args[0]
        # 第一个位置参数是 SQL
        sql = db.execute.call_args[0][0]
        assert "field_type" in sql

    def test_multiple_rows_mixed(self):
        """多行：部分需改、部分已合法。"""
        rows = [
            self._row(10, "picklist"),                              # 需改 → classify
            self._row(11, "picklist", af_extra={"mode": "classify"}), # 已合法
            self._row(12, "text", af_extra={"mission": "查最近融资"}),  # 需改 → web_agent
        ]
        db = _make_db(rows)
        changes = run_migration(db, apply=False)
        assert len(changes) == 2
        modes = {c["field_name"]: c["new_mode"] for c in changes}
        assert modes["f_10"] == "classify"
        assert modes["f_12"] == "web_agent"
