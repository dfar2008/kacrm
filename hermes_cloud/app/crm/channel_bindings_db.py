"""channel_bindings 数据访问层。

封装对 ``channel_bindings`` 表的 CRUD，用于将渠道 external_id（飞书
open_id 等）映射到 CRM 模块记录（P0b inbound 互动挂记录）。

设计要点：
- 所有函数接受 ``db: DbWrapper`` 作为第一参数，不自管事务
- 返回纯 dict / None / list，不引入 ORM 概念
- upsert 语义：同 (channel, external_id) 重复绑定时更新指向的记录
"""
from __future__ import annotations

from typing import Optional

from hermes.crm.db import DbWrapper


def get(db: DbWrapper, channel: str, external_id: str) -> Optional[dict]:
    """按 (channel, external_id) 查一条绑定记录，未找到返回 None。"""
    return db.fetchone(
        """
        SELECT id, channel, external_id, module, record_id, bound_by, created_at
          FROM channel_bindings
         WHERE channel = %s AND external_id = %s
        """,
        (channel, external_id),
    )


def bind(
    db: DbWrapper,
    *,
    channel: str,
    external_id: str,
    module: str,
    record_id: int,
    bound_by: Optional[int] = None,
) -> dict:
    """Upsert 一条绑定。

    若 (channel, external_id) 已存在则更新 module/record_id/bound_by，
    返回最终行（含 id、created_at）。
    """
    return db.fetchone(
        """
        INSERT INTO channel_bindings (channel, external_id, module, record_id, bound_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (channel, external_id) DO UPDATE
          SET module     = EXCLUDED.module,
              record_id  = EXCLUDED.record_id,
              bound_by   = EXCLUDED.bound_by
        RETURNING id, channel, external_id, module, record_id, bound_by, created_at
        """,
        (channel, external_id, module, record_id, bound_by),
    )


def list_for_record(db: DbWrapper, module: str, record_id: int) -> list[dict]:
    """返回某 (module, record_id) 下全部绑定行，按 created_at 升序。"""
    return db.fetchall(
        """
        SELECT id, channel, external_id, module, record_id, bound_by, created_at
          FROM channel_bindings
         WHERE module = %s AND record_id = %s
         ORDER BY created_at ASC
        """,
        (module, record_id),
    )


def unbind(db: DbWrapper, binding_id: int) -> None:
    """按主键删除一条绑定。"""
    db.execute(
        "DELETE FROM channel_bindings WHERE id = %s",
        (binding_id,),
    )
