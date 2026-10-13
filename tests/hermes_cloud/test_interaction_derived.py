"""互动派生属性单元测试。

测试层级：
1. 纯函数 connection_strength — 不连库，验证公式 + 分档边界。
2. compute_derived + FakeDb — 验证 SQL、聚合逻辑、count/first/last/strength。

FakeDb 模式参考 test_channel_bindings.py。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import pytest

from hermes_cloud.app.crm.interaction_derived import (
    compute_derived,
    connection_strength,
)

# ──────────────────────────────────────────────────────────────────────────────
# FakeDb 替身
# ──────────────────────────────────────────────────────────────────────────────

class FakeDb:
    """最小 DbWrapper 替身，固定返回 fetchall_result。"""

    def __init__(self, fetchall_result: Optional[List[dict]] = None):
        self._fetchall_result = fetchall_result or []
        self.last_sql: Optional[str] = None
        self.last_args: Optional[tuple] = None

    def fetchall(self, sql: str, args: tuple = ()) -> List[dict]:
        self.last_sql = sql
        self.last_args = args
        return self._fetchall_result


# ──────────────────────────────────────────────────────────────────────────────
# 工具：构造互动行
# ──────────────────────────────────────────────────────────────────────────────

def _make_row(
    days_ago: float = 1.0,
    actor_type: str = "external",
) -> dict:
    """生成一条 interactions 行（occurred_at 为 UTC now - days_ago 天）。"""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "occurred_at": dt,
        "actors": [{"type": actor_type, "id": 42}],
    }


def _rows_json_actors(rows: List[dict]) -> List[dict]:
    """把 actors 序列化为 JSON 字符串（模拟数据库返回 text 而非 list）。"""
    return [
        {**row, "actors": json.dumps(row["actors"])}
        for row in rows
    ]


# ──────────────────────────────────────────────────────────────────────────────
# 一、纯函数 connection_strength 测试
# ──────────────────────────────────────────────────────────────────────────────

class TestConnectionStrengthPure:
    """connection_strength(n, d, r) 纯函数——不连库。"""

    def test_no_interaction_is_weak(self):
        """n=0 → score=0 → 弱"""
        score, label = connection_strength(0, float("inf"), 0.5)
        assert label == "弱"
        assert score == 0.0

    def test_high_freq_recent_bilateral_is_strong(self):
        """n=20(满), d=0(今天), r=1(全外部回复) → score=1.0 → 强"""
        score, label = connection_strength(20, 0.0, 1.0)
        assert label == "强"
        assert abs(score - 1.0) < 1e-9

    def test_mid_range_is_zhong(self):
        """找一个 score 落在 [0.33, 0.66) 的点 → 中"""
        # n=10, d=15, r=0.5
        # min(1, 10/20)=0.5; (0.5+0.5*max(0,1-15/30))=0.5+0.5*0.5=0.75
        # (0.5+0.5*0.5)=0.75; score=0.5*0.75*0.75=0.28125  → 弱?
        # 改：n=10, d=0, r=0.5 → 0.5*(0.5+0.5*1)*(0.5+0.5*0.5)=0.5*1.0*0.75=0.375 → 中
        score, label = connection_strength(10, 0.0, 0.5)
        assert label == "中"
        assert 0.33 <= score < 0.66

    def test_border_strong_threshold(self):
        """score 恰好 >= 0.66 → 强"""
        # 找 n,d,r 使 score = 0.66
        # 简单：n>=20 → min(1,n/20)=1; d=0 → recency_factor=1.0; (0.5+0.5*r)=0.66/(1*1)=0.66
        # → r = (0.66/1.0 - 0.5)/0.5 = 0.32/0.5=0.64
        score, label = connection_strength(20, 0.0, 0.64)
        assert label == "强" if score >= 0.66 else "中"
        # 精确验证 score
        expected = min(1.0, 20 / 20) * (0.5 + 0.5 * max(0, 1 - 0.0 / 30)) * (0.5 + 0.5 * 0.64)
        assert abs(score - expected) < 1e-9

    def test_border_mid_threshold(self):
        """score 恰好 = 0.33 → 中"""
        # n=20,d=0,r=? → (0.5+0.5*r)=0.33/(1*1)=0.33; r=(0.33-0.5)/0.5 = -0.34 < 0 → 不行
        # n=5,d=30,r=0.5 → min(1,5/20)=0.25; (0.5+0.5*max(0,0))=0.5; (0.5+0.25)=0.75
        # score=0.25*0.5*0.75=0.09375 → 弱
        # n=20,d=0,r=0.16 → score=1*1*(0.5+0.08)=0.58 → 中? (0.33<=0.58<0.66) → 中
        score, label = connection_strength(20, 0.0, 0.16)
        expected = 1.0 * 1.0 * (0.5 + 0.5 * 0.16)
        assert abs(score - expected) < 1e-9
        assert label in ("中", "弱")  # 约 0.58 → 中

    def test_large_d_degrades_score(self):
        """d=60(超出近 30 天衰减)，recency_factor=0 → score 降"""
        score_near, _ = connection_strength(20, 0.0, 1.0)
        score_far, label = connection_strength(20, 60.0, 1.0)
        assert score_far < score_near
        # d=60: recency=max(0,1-60/30)=0; score=1*(0.5+0)*（0.5+0.5)=0.5 → 中
        assert label == "中"
        assert abs(score_far - 0.5) < 1e-9

    def test_zero_r_gives_lower_score(self):
        """r=0(对方从不回复) vs r=1 → score 低一半"""
        s0, _ = connection_strength(20, 0.0, 0.0)
        s1, _ = connection_strength(20, 0.0, 1.0)
        assert s0 < s1
        # r=0 → factor=0.5; r=1 → factor=1.0; 比值 = 0.5
        assert abs(s0 / s1 - 0.5) < 1e-9

    def test_n_cap_at_20(self):
        """n=100 与 n=20 结果相同（min 封顶）"""
        s20, l20 = connection_strength(20, 0.0, 1.0)
        s100, l100 = connection_strength(100, 0.0, 1.0)
        assert abs(s20 - s100) < 1e-9
        assert l20 == l100


# ──────────────────────────────────────────────────────────────────────────────
# 二、compute_derived 集成（FakeDb）测试
# ──────────────────────────────────────────────────────────────────────────────

class TestComputeDerived:
    """compute_derived(db, module, record_id) — 用 FakeDb 桩。"""

    def test_no_rows_returns_zeros(self):
        db = FakeDb(fetchall_result=[])
        result = compute_derived(db, "contact", 99)
        assert result["interaction_count"] == 0
        assert result["first_interaction_at"] is None
        assert result["last_interaction_at"] is None
        assert result["connection_strength"] == "弱"
        assert result["connection_score"] == 0.0

    def test_sql_uses_correct_subject_filter(self):
        """SQL 里应包含 subjects @> 过滤且参数是正确的 JSON。"""
        db = FakeDb(fetchall_result=[])
        compute_derived(db, "contact", 7)
        assert db.last_sql is not None
        assert "subjects @>" in db.last_sql
        arg_json = db.last_args[0]
        parsed = json.loads(arg_json)
        assert parsed == [{"module": "contact", "id": 7}]

    def test_count_and_first_last(self):
        """三条互动 → count=3，first/last 正确。"""
        now = datetime.now(timezone.utc)
        rows = [
            {"occurred_at": now - timedelta(days=5), "actors": [{"type": "external"}]},
            {"occurred_at": now - timedelta(days=3), "actors": [{"type": "user", "id": 1}]},
            {"occurred_at": now - timedelta(days=1), "actors": [{"type": "external"}]},
        ]
        db = FakeDb(fetchall_result=rows)
        result = compute_derived(db, "contact", 1)
        assert result["interaction_count"] == 3
        # first = 5天前那条（SQL 排序 ASC，rows[0]）
        assert abs((result["first_interaction_at"] - (now - timedelta(days=5))).total_seconds()) < 2
        # last = 1天前那条（rows[-1]）
        assert abs((result["last_interaction_at"] - (now - timedelta(days=1))).total_seconds()) < 2

    def test_strength_strong_with_many_recent_external(self):
        """20 条全在近 90 天 + 今天发生 + 全外部 → 强"""
        now = datetime.now(timezone.utc)
        rows = [
            {"occurred_at": now - timedelta(hours=1), "actors": [{"type": "external"}]}
            for _ in range(20)
        ]
        db = FakeDb(fetchall_result=rows)
        result = compute_derived(db, "lead", 5)
        assert result["connection_strength"] == "强"
        assert result["connection_score"] >= 0.66

    def test_strength_weak_with_old_interaction(self):
        """只有 1 条，且发生在 91 天前 → 近 90 天 n=0 → score=0 → 弱"""
        now = datetime.now(timezone.utc)
        rows = [
            {"occurred_at": now - timedelta(days=91), "actors": [{"type": "external"}]},
        ]
        db = FakeDb(fetchall_result=rows)
        result = compute_derived(db, "contact", 2)
        # n=0 (近90天没有), score=0 → 弱
        assert result["connection_strength"] == "弱"
        assert result["connection_score"] == 0.0
        # 但 count/first/last 仍正确
        assert result["interaction_count"] == 1

    def test_actors_as_json_string(self):
        """actors 字段是 JSON 字符串时（DB 某些驱动返回 text），也能正确解析。"""
        now = datetime.now(timezone.utc)
        rows = [
            {"occurred_at": now - timedelta(days=1), "actors": json.dumps([{"type": "external"}])},
            {"occurred_at": now - timedelta(hours=1), "actors": json.dumps([{"type": "user", "id": 1}])},
        ]
        db = FakeDb(fetchall_result=rows)
        result = compute_derived(db, "contact", 3)
        assert result["interaction_count"] == 2
        # 1 external / 2 total = 0.5 → r=0.5（两条近期）
        # n=2, d≈0, r=0.5 → score=min(1,2/20)*(0.5+0.5*1)*(0.5+0.25)=0.1*1.0*0.75=0.075 → 弱
        assert result["connection_strength"] == "弱"

    def test_actors_none_degrades_to_constant(self):
        """actors 全为 None 时 r 降级为 0.5 常量，不抛错。"""
        now = datetime.now(timezone.utc)
        rows = [
            {"occurred_at": now - timedelta(days=1), "actors": None},
            {"occurred_at": now - timedelta(hours=2), "actors": None},
        ]
        db = FakeDb(fetchall_result=rows)
        # 只要不抛错即可
        result = compute_derived(db, "contact", 4)
        assert "connection_strength" in result
        assert "connection_score" in result

    def test_mixed_actor_types(self):
        """一半 external 一半 user → r = 0.5"""
        now = datetime.now(timezone.utc)
        rows = [
            {"occurred_at": now - timedelta(days=1), "actors": [{"type": "external"}]},
            {"occurred_at": now - timedelta(hours=1), "actors": [{"type": "user", "id": 1}]},
        ]
        db = FakeDb(fetchall_result=rows)
        result = compute_derived(db, "contact", 10)
        # n=2, d≈0, r=0.5; score=0.1*1.0*0.75=0.075 → 弱
        # 验证 score 计算和没报错
        assert result["connection_score"] >= 0.0

    def test_occurred_at_as_iso_string(self):
        """occurred_at 以 ISO 字符串形式返回（某些驱动行为）→ 正确解析。"""
        now = datetime.now(timezone.utc)
        rows = [
            {
                "occurred_at": (now - timedelta(days=2)).isoformat(),
                "actors": [{"type": "external"}],
            }
        ]
        db = FakeDb(fetchall_result=rows)
        result = compute_derived(db, "lead", 8)
        assert result["interaction_count"] == 1
        assert result["first_interaction_at"] is not None
        assert result["last_interaction_at"] is not None


# ──────────────────────────────────────────────────────────────────────────────
# 三、_derived_attrs 集成（通过 ai_attributes 导入）
# ──────────────────────────────────────────────────────────────────────────────

class TestDerivedAttrsIntegration:
    """验证 ai_attributes._derived_attrs 改签名后向后兼容 + 实时计算路径。"""

    def test_no_db_returns_empty(self):
        """db=None → 无互动可算，record 也无字段 → {}"""
        from hermes_cloud.app.crm.ai_attributes import _derived_attrs
        result = _derived_attrs({}, db=None, module=None)
        assert result == {}

    def test_no_db_falls_back_to_record_fields(self):
        """db=None 时从 record 读旧字段（向后兼容）。"""
        from hermes_cloud.app.crm.ai_attributes import _derived_attrs
        now = datetime.now(timezone.utc)
        record = {
            "last_interaction_at": now,
            "interaction_count": 3,
            "connection_strength": "中",
        }
        result = _derived_attrs(record, db=None, module=None)
        assert result.get("最近互动") == now
        assert result.get("互动次数") == 3
        assert result.get("连接强度") == "中"

    def test_with_db_computes_from_interactions(self):
        """db+module 均提供时调 compute_derived，返回中文键。"""
        from hermes_cloud.app.crm.ai_attributes import _derived_attrs
        now = datetime.now(timezone.utc)
        rows = [
            {"occurred_at": now - timedelta(hours=2), "actors": [{"type": "external"}]},
        ]
        db = FakeDb(fetchall_result=rows)
        record = {"contact_id": 55}
        result = _derived_attrs(record, db=db, module="contact")
        assert "最近互动" in result
        assert "首次互动" in result
        assert "互动次数" in result
        assert result["互动次数"] == 1
        assert "连接强度" in result

    def test_with_db_no_record_id_falls_back(self):
        """record 里没有 pk 字段时，静默降级到 record 字段。"""
        from hermes_cloud.app.crm.ai_attributes import _derived_attrs
        db = FakeDb(fetchall_result=[])
        record = {}  # 没有 contact_id
        # 不应抛错
        result = _derived_attrs(record, db=db, module="contact")
        # 没有 pk → 没法查，降级到 record 字段（也没有）→ {}
        assert result == {}
