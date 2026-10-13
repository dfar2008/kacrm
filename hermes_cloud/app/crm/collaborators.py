"""record_collaborators 数据访问层。

封装对 ``record_collaborators`` 表的 CRUD,被 routes 和
``list_query`` 行级过滤逻辑共同复用。

设计要点:
- 所有函数接受 ``db: DbWrapper`` 作为第一参数,不自管事务
- 返回纯 dict / set,不引入 ORM 概念
- ACL 决策不在此层做 —— 此层只做"按主键查/写";调用方拼装权限
"""
from __future__ import annotations

from typing import Any

from hermes.crm.db import DbWrapper


def list_collaborators(db: DbWrapper, *, module_id: int, record_id: int) -> list[dict[str, Any]]:
    """返回某 (module, record) 的全部 collaborator 行,含 user 基础信息。"""
    return db.fetchall(
        """
        SELECT rc.user_id, u.username, u.realname,
               rc.can_view, rc.can_edit, rc.created_at
          FROM record_collaborators rc
          JOIN users u ON u.id = rc.user_id
         WHERE rc.module_id = %s AND rc.record_id = %s
         ORDER BY rc.created_at ASC
        """,
        (module_id, record_id),
    )


def upsert_collaborator(
    db: DbWrapper, *, module_id: int, record_id: int, user_id: int,
    can_view: bool, can_edit: bool,
) -> None:
    """Upsert 一条 collaborator 授权(同三元组已存在则覆盖权限位)。"""
    db.execute(
        """
        INSERT INTO record_collaborators
            (module_id, record_id, user_id, can_view, can_edit)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (module_id, record_id, user_id) DO UPDATE
          SET can_view = EXCLUDED.can_view,
              can_edit = EXCLUDED.can_edit
        """,
        (module_id, record_id, user_id, can_view, can_edit),
    )


def delete_collaborator(
    db: DbWrapper, *, module_id: int, record_id: int, user_id: int,
) -> bool:
    """删除一条 collaborator 授权。返回是否删到 (true if 命中一行)。"""
    affected = db.execute(
        """
        DELETE FROM record_collaborators
         WHERE module_id = %s AND record_id = %s AND user_id = %s
        """,
        (module_id, record_id, user_id),
    )
    return affected > 0


def shared_record_ids_for_user(db: DbWrapper, *, user_id: int, module_id: int) -> set[int]:
    """返回 user 在该 module 下作为 collaborator 可见的 record_id 集合。

    用于 list / detail 行级过滤的第二支可见性来源。空集表示用户没被
    共享任何该模块的记录。
    """
    rows = db.fetchall(
        """
        SELECT record_id
          FROM record_collaborators
         WHERE user_id = %s AND module_id = %s
           AND (can_view OR can_edit)
        """,
        (user_id, module_id),
    )
    return {int(r["record_id"]) for r in rows}


def user_can_edit_record(
    db: DbWrapper, *, user_id: int, module_id: int, record_id: int,
) -> bool:
    """判断 user 是否被共享 + can_edit=TRUE。写侧 ACL 用。"""
    row = db.fetchone(
        """
        SELECT can_edit FROM record_collaborators
         WHERE module_id = %s AND record_id = %s AND user_id = %s
        """,
        (module_id, record_id, user_id),
    )
    return bool(row and row.get("can_edit"))
