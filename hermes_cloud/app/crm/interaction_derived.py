"""互动派生属性计算模块。

从 interactions 表中按记录维度聚合：
  - first_interaction_at / last_interaction_at
  - interaction_count
  - connection_strength ('强'|'中'|'弱') / connection_score (0..1)

connection_strength 公式：
  n = 近 90 天互动条数
  d = 距最近一次互动的天数（无互动→无穷大）
  r = external 方发送占比（actors 里 type=='external' 的条数 / 总条数）
      若所有 actors 均无法区分类型，降级为 0.5 常量。
  score = min(1.0, n/20) * (0.5 + 0.5 * max(0, 1 - d/30)) * (0.5 + 0.5*r)
  分档：score >= 0.66 → '强'；>= 0.33 → '中'；else → '弱'
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 纯函数：连接强度公式
# ──────────────────────────────────────────────────────────────────────────────

def connection_strength(n: int, d: float, r: float) -> Tuple[float, str]:
    """计算连接强度得分与档位（纯函数，不连库）。

    Args:
        n: 近 90 天互动条数（>= 0）
        d: 距最近一次互动的天数（无互动传 float('inf') 或很大的值）
        r: external 方发送占比（0..1；信息不足时调用方传 0.5）

    Returns:
        (score: float 0..1, label: '强'|'中'|'弱')
    """
    recency = max(0.0, 1.0 - d / 30.0)
    score = min(1.0, n / 20.0) * (0.5 + 0.5 * recency) * (0.5 + 0.5 * r)
    if score >= 0.66:
        label = "强"
    elif score >= 0.33:
        label = "中"
    else:
        label = "弱"
    return score, label


# ──────────────────────────────────────────────────────────────────────────────
# 辅助：从 actors jsonb 列解析回复占比
# ──────────────────────────────────────────────────────────────────────────────

def _parse_actors(actors_value: Any) -> List[dict]:
    """把 DB 行里的 actors 字段统一转成 list[dict]。"""
    if actors_value is None:
        return []
    if isinstance(actors_value, list):
        return actors_value
    if isinstance(actors_value, str):
        try:
            parsed = json.loads(actors_value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _external_ratio(rows: List[dict]) -> Tuple[float, bool]:
    """计算 external actor 发送占比。

    Returns:
        (r: float, degraded: bool)
        degraded=True 表示无法区分 type → 降级到 0.5 常量。
    """
    if not rows:
        return 0.5, True

    has_type = False
    external_count = 0
    for row in rows:
        actors = _parse_actors(row.get("actors"))
        for actor in actors:
            t = str(actor.get("type") or "").lower()
            if t in ("external", "user"):
                has_type = True
            if t == "external":
                external_count += 1

    if not has_type:
        return 0.5, True  # 降级

    total = len(rows)
    r = external_count / total if total > 0 else 0.5
    return r, False


# ──────────────────────────────────────────────────────────────────────────────
# 主函数：查库 + 调公式
# ──────────────────────────────────────────────────────────────────────────────

_NINETY_DAYS_SEC = 90 * 24 * 3600


def compute_derived(db, module: str, record_id: int) -> dict:
    """据 interactions 算派生属性。

    查询 subjects @> '[{"module":..,"id":..}]' 的全部互动行（无上限，派生计算用）。

    Returns:
        {
            "first_interaction_at": datetime | None,
            "last_interaction_at": datetime | None,
            "interaction_count": int,
            "connection_strength": '强'|'中'|'弱',
            "connection_score": float,
        }
    无互动时 count=0, strength='弱', score=0.0, *_at=None。
    """
    subject_filter = json.dumps([{"module": module, "id": record_id}])
    rows: List[dict] = db.fetchall(
        "SELECT occurred_at, actors FROM interactions "
        "WHERE subjects @> %s::jsonb ORDER BY occurred_at ASC",
        (subject_filter,),
    ) or []

    if not rows:
        return {
            "first_interaction_at": None,
            "last_interaction_at": None,
            "interaction_count": 0,
            "connection_strength": "弱",
            "connection_score": 0.0,
        }

    # first / last / count
    interaction_count = len(rows)

    def _parse_dt(v) -> Optional[datetime]:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    first_at = _parse_dt(rows[0].get("occurred_at"))
    last_at = _parse_dt(rows[-1].get("occurred_at"))

    # 近 90 天行
    now = datetime.now(timezone.utc)
    ninety_days_rows = []
    for row in rows:
        dt = _parse_dt(row.get("occurred_at"))
        if dt and (now - dt).total_seconds() <= _NINETY_DAYS_SEC:
            ninety_days_rows.append(row)
    n = len(ninety_days_rows)

    # d = 距最近互动天数
    if last_at is not None:
        d = (now - last_at).total_seconds() / 86400.0
    else:
        d = float("inf")

    # r = external 发送占比（用近 90 天行计算；无近期行则用全部）
    source_rows = ninety_days_rows if ninety_days_rows else rows
    r, degraded = _external_ratio(source_rows)
    if degraded:
        logger.debug(
            "interaction_derived: actors type 无法区分，r 降级为 0.5 "
            "(module=%s record_id=%s)", module, record_id
        )

    score, label = connection_strength(n, d, r)

    return {
        "first_interaction_at": first_at,
        "last_interaction_at": last_at,
        "interaction_count": interaction_count,
        "connection_strength": label,
        "connection_score": round(score, 4),
    }
