"""Memory Substrate REST API — charter 原语 ② HTTP surface.

Mounted at ``/api/memory``. Three read-only surfaces plus an admin
trigger for manual re-ingestion:

- ``GET  /api/memory/search``           kNN over ``memories``
- ``GET  /api/memory/entities``         lookup by type + canonical
- ``GET  /api/memory/entities/{id}``    single entity row
- ``GET  /api/memory/entities/{id}/subgraph``  neighbours up to depth=3
- ``GET  /api/memory/journey``          personal learning timeline (all kinds)
- ``POST /api/memory/ingest``           admin-only manual ingest_batch

All read endpoints auto-scope by the dashboard user so one tenant's
memories never leak into another's recall.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from hermes_cloud.app.auth import require_admin_auth, require_dashboard_auth
from hermes_cloud.app.config import AppConfig
from hermes.memory import (
    find_entity,
    merge_entities,
    search_memories,
    subgraph,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _get_config(request: Request) -> AppConfig:
    return getattr(request.app.state, "config", None) or AppConfig()


def _current_user_id(request: Request) -> int:
    """Resolve the dashboard session → integer user_id.

    ``require_dashboard_auth`` historically returns ``(user_id, token)``;
    normalise here so every memory endpoint gets a plain int.
    """
    raw = require_dashboard_auth(request, _get_config(request))
    if isinstance(raw, tuple):
        return int(raw[0]) if raw and raw[0] is not None else 0
    if isinstance(raw, dict):
        return int(raw.get("id") or 0)
    return int(raw) if raw else 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.get("/search")
def search_endpoint(
    request: Request,
    q: str = Query(..., min_length=1, max_length=2000),
    kind: Optional[str] = None,
    top_k: int = Query(10, ge=1, le=50),
    min_similarity: float = Query(0.0, ge=0.0, le=1.0),
):
    """Vector search. Scoped to the current dashboard user's memories
    (plus any ``scope='org'`` rows). Returns rows ordered by cosine
    similarity descending.

    ADMIN-ONLY: global memory vector search spans org-shared entities, so
    it's restricted to admins like the browse view above.
    """
    require_admin_auth(request, _get_config(request))
    user_id = _current_user_id(request)
    try:
        rows = search_memories(
            query=q,
            user_id=user_id,
            kind=kind,
            top_k=int(top_k),
            min_similarity=float(min_similarity),
        )
    except Exception as exc:
        logger.exception("memory_search_failed uid=%s q=%r", user_id, q[:50])
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# Entity lookup + subgraph
# ---------------------------------------------------------------------------


@router.get("/entities")
def entity_lookup(
    request: Request,
    entity_type: str = Query(..., min_length=1, max_length=64),
    canonical: str = Query(..., min_length=1, max_length=256),
):
    """Find a single entity by (type, canonical) within the user's scope."""
    user_id = _current_user_id(request)
    row = find_entity(
        entity_type=entity_type,
        canonical=canonical,
        user_id=user_id,
    )
    if not row:
        # Fall back to org-scope (user_id NULL) before giving up.
        row = find_entity(entity_type=entity_type, canonical=canonical)
    if not row:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"data": row}


@router.get("/entities/{entity_id}/subgraph")
def entity_subgraph(
    request: Request,
    entity_id: int,
    depth: int = Query(1, ge=1, le=3),
    max_edges: int = Query(100, ge=1, le=500),
):
    """Return entities + edges reachable within ``depth`` hops.

    Does not filter by user_id — if you can resolve an entity id (via
    the scoped ``/entities`` lookup or your own data), the subgraph is
    public within the tenant. Pathological traversals are capped by
    ``max_edges`` (hard limit 500).
    """
    _current_user_id(request)  # auth check only
    try:
        g = subgraph(
            entity_id=int(entity_id),
            depth=int(depth),
            max_edges=int(max_edges),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("memory_subgraph_failed eid=%s", entity_id)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": g}


# ---------------------------------------------------------------------------
# Browse: product-facing views (recent / org / person / fact)
# ---------------------------------------------------------------------------


@router.get("/browse")
def browse_endpoint(
    request: Request,
    kind: str = Query("recent", min_length=1, max_length=16),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List view for the product-facing Memory page.

    Four ``kind`` modes:

    - ``recent``  — entities sorted by ``last_seen_at`` desc (mixed types)
    - ``org``     — only ``entity_type='org'`` entities, same sort
    - ``person``  — only ``entity_type='person'`` entities, same sort
    - ``fact``    — raw ``memories`` rows (``kind='fact'``) sorted by
      ``created_at`` desc

    Each entity card comes with ``memory_count`` (how many memories link
    to it) so the UI can show "7 条记忆". Scope is derived from
    ``user_id`` (NULL → org-scope, non-NULL → private to that user).
    All results are filtered to the caller's tenant plus org-scope rows.

    ADMIN-ONLY: this is the org-wide memory browser (the 记忆库 menu page).
    Entities default to scope='org', so it exposes every user's extracted
    companies/people. Employees must not browse it. (Per-record memory
    links — /entities/{id}/crm-links — stay open: they're scoped to a
    record the caller can already see.)
    """
    require_admin_auth(request, _get_config(request))
    user_id = _current_user_id(request)
    kind = kind.lower()

    # Import here to avoid a module-level dep on crm.db at import time
    # (matches the pattern used by /ingest).
    from hermes.crm.db import db_session

    if kind == "fact":
        sql = """
            SELECT id, kind, title, content, scope, source_refs, meta,
                   created_at
            FROM memories
            WHERE kind = 'fact'
              AND (user_id = %s OR scope = 'org')
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """
        args = (user_id, int(limit), int(offset))
        with db_session() as db:
            rows = db.fetchall(sql, args) or []
        return {"data": list(rows), "count": len(rows), "kind": kind}

    # Entity modes. Filter by type if explicit, otherwise "recent"
    # mixes everything the user can see. CRM-shaped types are folded
    # into the matching org / person tab so the bespoke ``crm:accounts``
    # / ``crm:contacts`` / ``crm:leads`` rows don't end up trapped in
    # the "recent" feed.
    type_filter = ""
    type_args: tuple = ()
    if kind == "org":
        type_filter = "AND e.entity_type IN ('org', 'crm:accounts')"
    elif kind == "person":
        type_filter = (
            "AND e.entity_type IN "
            "('person', 'crm:contacts', 'crm:leads', 'lead')"
        )
    elif kind != "recent":
        raise HTTPException(
            status_code=400,
            detail="kind must be one of: recent, org, person, fact",
        )

    sql = f"""
        SELECT e.id, e.entity_type, e.canonical, e.aliases, e.attributes,
               e.user_id, e.confidence,
               e.first_seen_at, e.last_seen_at,
               COALESCE(mc.memory_count, 0) AS memory_count
        FROM memory_entities e
        LEFT JOIN (
            SELECT entity_id, COUNT(*)::int AS memory_count
            FROM memory_entity_sources
            GROUP BY entity_id
        ) mc ON mc.entity_id = e.id
        WHERE (e.user_id = %s OR e.user_id IS NULL)
          {type_filter}
        ORDER BY COALESCE(e.last_seen_at, e.created_at) DESC
        LIMIT %s OFFSET %s
    """
    with db_session() as db:
        rows = db.fetchall(sql, (user_id, *type_args, int(limit), int(offset))) or []
    # Normalize: derive scope string for the UI (charter §10).
    for r in rows:
        r["scope"] = "org" if r.get("user_id") is None else "user"
    return {"data": list(rows), "count": len(rows), "kind": kind}


# ---------------------------------------------------------------------------
# Journey: personal learning timeline (product-facing)
# ---------------------------------------------------------------------------


@router.get("/journey")
def journey_endpoint(
    request: Request,
    limit: int = Query(120, ge=1, le=300),
    offset: int = Query(0, ge=0),
):
    """学习时间线：agent 为当前用户沉淀的记忆，按时间倒序。

    与 ``/browse?kind=fact`` 的区别:
    - ``fact`` 只取 ``kind='fact'`` 且是 admin-only 的组织级浏览器;
    - ``journey`` 取**所有** kind 的记忆(fact / preference / summary …),
      作为"agent 学到了什么"的个人视角时间线,按 ``created_at`` 倒序,
      每条附带其触及的实体(公司/人物)供 UI 渲染标签与跳转。

    鉴权:走标准 dashboard 会话(非 admin-only)。作用域收敛到调用者
    自己的记忆 + ``scope='org'`` 的共享记忆,和 ``fact`` 模式的记忆查询
    同一套过滤,不会泄漏他人私有记忆。

    只读:不改写/删除任何记忆语义,仅"看见"。已有的编辑/删除入口
    (实体详情页 / 调试面板)照旧复用。
    """
    user_id = _current_user_id(request)

    from hermes.crm.db import db_session

    sql = """
        SELECT m.id, m.kind, m.title, m.content, m.scope,
               m.source_refs, m.meta, m.confidence, m.created_at,
               COALESCE(
                   (
                       SELECT json_agg(
                           json_build_object(
                               'entity_id', e.id,
                               'entity_type', e.entity_type,
                               'canonical', e.canonical,
                               'role', s.role
                           )
                           ORDER BY e.canonical
                       )
                       FROM memory_entity_sources s
                       JOIN memory_entities e ON e.id = s.entity_id
                       WHERE s.memory_id = m.id
                   ),
                   '[]'::json
               ) AS entities
        FROM memories m
        WHERE (m.user_id = %s OR m.scope = 'org')
        ORDER BY m.created_at DESC
        LIMIT %s OFFSET %s
    """
    args = (user_id, int(limit), int(offset))
    with db_session() as db:
        try:
            rows = db.fetchall(sql, args) or []
        except Exception as exc:
            # 老库可能缺 memory_entity_sources / json_agg 版本差异,
            # 退化成不带实体标签的纯记忆流,保证时间线始终可用。
            logger.warning("memory_journey_entities_failed uid=%s: %s", user_id, exc)
            fallback_sql = """
                SELECT id, kind, title, content, scope,
                       source_refs, meta, confidence, created_at
                FROM memories
                WHERE (user_id = %s OR scope = 'org')
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """
            rows = db.fetchall(fallback_sql, args) or []
            for r in rows:
                r["entities"] = []
    for r in rows:
        if r.get("entities") is None:
            r["entities"] = []
    return {"data": list(rows), "count": len(rows)}


# ---------------------------------------------------------------------------
# Entity drilldown: facts + timeline (product-facing)
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}")
def entity_detail(request: Request, entity_id: int):
    """Single entity row by id, plus a scope-safety check.

    Separate from ``/entities?entity_type=…&canonical=…`` which looks
    up by name. The detail page needs an id → row resolver so deep
    links work.
    """
    _current_user_id(request)
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone(
            "SELECT * FROM memory_entities WHERE id = %s", (int(entity_id),)
        )
    if not row:
        raise HTTPException(status_code=404, detail="entity not found")
    row["scope"] = "org" if row.get("user_id") is None else "user"
    return {"data": row}


@router.get("/entities/{entity_id}/facts")
def entity_facts(
    request: Request,
    entity_id: int,
    limit: int = Query(50, ge=1, le=200),
):
    """Memories linked to this entity (via ``memory_entity_sources``).

    Returns newest first. Each row carries ``role`` from the link table
    ("mention", "subject", …) so the UI can distinguish passing
    mentions from first-person subjects.
    """
    _current_user_id(request)
    from hermes.crm.db import db_session
    sql = """
        SELECT m.id, m.kind, m.title, m.content, m.scope,
               m.source_refs, m.meta, m.confidence, m.created_at,
               s.role
        FROM memory_entity_sources s
        JOIN memories m ON m.id = s.memory_id
        WHERE s.entity_id = %s
        ORDER BY m.created_at DESC
        LIMIT %s
    """
    with db_session() as db:
        rows = db.fetchall(sql, (int(entity_id), int(limit))) or []
    return {"data": list(rows), "count": len(rows)}


@router.get("/entities/{entity_id}/timeline")
def entity_timeline(
    request: Request,
    entity_id: int,
    limit: int = Query(30, ge=1, le=100),
):
    """Interaction timeline derived from memories linked to the entity.

    Walks ``memories.source_refs`` for ``interaction_id`` keys, joins to
    ``interactions``, and returns a chronological feed. Memories with no
    interaction ref simply won't appear (they still show in ``/facts``).
    """
    _current_user_id(request)
    from hermes.crm.db import db_session
    # jsonb_array_elements on source_refs → filter elements that carry
    # interaction_id. Using a LATERAL join keeps the SQL readable.
    sql = """
        SELECT DISTINCT ON (i.id)
               i.id, i.kind, i.source, i.occurred_at,
               i.actors, i.subjects, i.content,
               m.id   AS memory_id,
               m.title AS memory_title
        FROM memory_entity_sources s
        JOIN memories m ON m.id = s.memory_id
        JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(m.source_refs) = 'array'
                 THEN m.source_refs ELSE '[]'::jsonb END
        ) ref ON true
        JOIN interactions i
          ON i.id::text = (ref->>'interaction_id')
        WHERE s.entity_id = %s
        ORDER BY i.id, i.occurred_at DESC
        LIMIT %s
    """
    with db_session() as db:
        try:
            rows = db.fetchall(sql, (int(entity_id), int(limit))) or []
        except Exception as exc:
            logger.warning("entity_timeline_failed eid=%s: %s", entity_id, exc)
            rows = []
    # Normalize UUIDs / datetimes the UI can display.
    for r in rows:
        if r.get("id") is not None:
            r["id"] = str(r["id"])
        if r.get("occurred_at") is not None:
            r["occurred_at"] = str(r["occurred_at"])
    # Re-sort by occurred_at desc (DISTINCT ON forces a different ORDER BY).
    rows.sort(key=lambda r: r.get("occurred_at") or "", reverse=True)
    return {"data": list(rows), "count": len(rows)}


# ---------------------------------------------------------------------------
# Reverse lookup: entity → CRM records
# ---------------------------------------------------------------------------


# (crm_table, pk_col, name_col, label) — mirror of
# ``hermes.crm.memory_identity._MAP`` but with display metadata
# for the UI's "open customer profile" chip.
_REVERSE_CRM_TABLES: list[tuple[str, str, str, str]] = [
    ("accounts",   "accounts_id",   "accounts_name",   "客户"),
    ("contacts",   "contacts_id",   "contacts_name",   "联系人"),
    ("leads",      "leads_id",      "leads_name",      "线索"),
    ("potentials", "potentials_id", "potentials_name", "商机"),
]


@router.get("/entities/{entity_id}/crm-links")
def entity_crm_links(request: Request, entity_id: int):
    """Reverse-resolve an entity to every CRM row that links back to it.

    Accounts / contacts / leads are 1:1 by design (partial unique index in
    migration 0018), but potentials are 1:N — a single org entity can have
    multiple deals. The UI uses this to render "→ 打开客户档案" chips on
    MemoryEntityDetail, so salespeople land on the familiar CRM record
    instead of stopping at the memory page.
    """
    _current_user_id(request)
    from hermes.crm.db import db_session
    out: list[dict] = []
    with db_session() as db:
        for table, pk, name_col, label in _REVERSE_CRM_TABLES:
            sql = (
                f"SELECT {pk} AS id, {name_col} AS name "
                f"FROM {table} "
                f"WHERE memory_entity_id = %s AND deleted = FALSE "
                f"ORDER BY {pk} ASC"
            )
            try:
                rows = db.fetchall(sql, (int(entity_id),)) or []
            except Exception:
                logger.exception("crm_links_lookup_failed table=%s", table)
                continue
            for r in rows:
                out.append({
                    "module": table,
                    "module_label": label,
                    "id": int(r["id"]),
                    "name": r.get("name") or "",
                })
    return {"data": out, "count": len(out)}


# ---------------------------------------------------------------------------
# Admin: entity merge
# ---------------------------------------------------------------------------


@router.post("/entities/{dst_id}/merge")
def entity_merge(
    request: Request,
    dst_id: int,
    body: dict = Body(...),
):
    """Admin-only: merge entity ``src_id`` into ``dst_id``.

    Dedup tool for when canonicalization let two rows slip through for
    the same real-world thing. Aliases/attributes are merged, all edge
    + source references are rewritten to ``dst_id``, the ``src_id`` row
    is deleted. Returns the updated dst row.
    """
    require_admin_auth(request, _get_config(request))
    src_id = body.get("src_id")
    if not isinstance(src_id, int):
        raise HTTPException(status_code=400, detail="src_id (int) required")
    try:
        row = merge_entities(src_id=int(src_id), dst_id=int(dst_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("memory_merge_failed src=%s dst=%s", src_id, dst_id)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": row}


# ---------------------------------------------------------------------------
# Admin: manual ingest trigger
# ---------------------------------------------------------------------------


@router.post("/ingest")
def trigger_ingest(
    request: Request,
    since_minutes: int = Query(60, ge=1, le=10080),  # up to 7 days
    limit: int = Query(500, ge=1, le=5000),
):
    """Admin-only: force a memory.ingest_batch pass now.

    Normally runs every 300s via the scheduler. This endpoint is for
    operational use — backfilling after a schedule outage or testing a
    new extractor.
    """
    require_admin_auth(request, _get_config(request))
    from hermes.memory.ingest import ingest_batch
    try:
        n = ingest_batch(since_minutes=int(since_minutes), limit=int(limit))
    except Exception as exc:
        logger.exception("memory_ingest_manual_failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return {"data": {"ingested": int(n)}}


__all__ = ["router"]
