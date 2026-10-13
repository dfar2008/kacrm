"""crm_memory_write 工具 + memory.write 实体链接 测试。"""
from __future__ import annotations

import secrets
from unittest.mock import ANY, MagicMock

import pytest


# --------------------------------------------------------------------------
# Task 1 — resolve_record_entity（纯逻辑,MagicMock db）
# --------------------------------------------------------------------------

def test_resolve_record_entity_unknown_table():
    from hermes.crm.memory_identity import resolve_record_entity
    assert resolve_record_entity(MagicMock(), "not_a_table", 1) == (False, None)


def test_resolve_record_entity_record_missing():
    from hermes.crm.memory_identity import resolve_record_entity
    db = MagicMock()
    db.fetchone.return_value = None
    assert resolve_record_entity(db, "accounts", 999) == (False, None)


def test_resolve_record_entity_null_entity():
    from hermes.crm.memory_identity import resolve_record_entity
    db = MagicMock()
    db.fetchone.return_value = {"memory_entity_id": None}
    assert resolve_record_entity(db, "accounts", 7) == (True, None)


def test_resolve_record_entity_linked():
    from hermes.crm.memory_identity import resolve_record_entity
    db = MagicMock()
    db.fetchone.return_value = {"memory_entity_id": 42}
    assert resolve_record_entity(db, "contacts", 5) == (True, 42)


# --------------------------------------------------------------------------
# resolve_for_insert — 1:1 占用守卫（纯逻辑,fake db）
#
# uniq_<table>_memory_entity（迁移 0018,部分唯一索引:活记录)强制
# accounts/contacts/leads 与 memory_entity 1:1。同名第二条记录经
# upsert_entity 复用同一实体 → 若实体已被同表活记录占用,盲插会撞唯一
# 索引(线索转客户 500 的根因)。resolve_for_insert 必须镜像 0018 回填
# 的「匹配尚未被占用的实体」守卫:占用了就返回 None(新行 memory_entity_id
# 置 NULL,合法且无损),而不是返回会撞索引的实体 id。
# --------------------------------------------------------------------------

class _FakeIdentityDb:
    """路由 upsert_entity 与占用检查两类查询的可控 db。"""

    def __init__(self, *, existing_entity=None, claimed=False, new_id=99):
        self.existing_entity = existing_entity
        self.claimed = claimed
        self.new_id = new_id
        self.claim_checks: list = []

    def fetchone(self, sql, args=None):
        s = " ".join(sql.split())
        if "FROM memory_entities" in s and "lower(canonical)" in s:
            return self.existing_entity  # upsert_entity 查重
        if "INSERT INTO memory_entities" in s:
            return {"id": self.new_id}   # upsert_entity 新建
        if "memory_entity_id = %s" in s and "LIMIT 1" in s:
            self.claim_checks.append((s, args))  # 占用检查
            return {"?column?": 1} if self.claimed else None
        return None

    def execute(self, sql, args=None):
        return None


def test_resolve_for_insert_returns_null_when_entity_claimed():
    """实体已被同表活记录占用 → None(新行置 NULL,不撞唯一索引)。"""
    from hermes.crm.memory_identity import resolve_for_insert
    db = _FakeIdentityDb(existing_entity={"id": 34}, claimed=True)
    out = resolve_for_insert(db, "contacts", {"contacts_name": "王经理"})
    assert out is None
    # 确实查过占用,且查的是 entity 34
    assert db.claim_checks and db.claim_checks[0][1] == (34,)


def test_resolve_for_insert_links_when_entity_free():
    """实体未被占用 → 返回实体 id 正常链接。"""
    from hermes.crm.memory_identity import resolve_for_insert
    db = _FakeIdentityDb(existing_entity={"id": 34}, claimed=False)
    out = resolve_for_insert(db, "contacts", {"contacts_name": "王经理"})
    assert out == 34


def test_resolve_for_insert_links_new_entity():
    """全新名(upsert 新建实体)→ 不可能被占用 → 返回新 id。"""
    from hermes.crm.memory_identity import resolve_for_insert
    db = _FakeIdentityDb(existing_entity=None, new_id=77, claimed=False)
    out = resolve_for_insert(db, "accounts", {"accounts_name": "新公司"})
    assert out == 77


def test_resolve_for_insert_no_guard_for_potentials():
    """potentials 非 1:1(无唯一索引)→ 不应用占用守卫,照常共享实体。"""
    from hermes.crm.memory_identity import resolve_for_insert
    db = _FakeIdentityDb(existing_entity={"id": 50}, claimed=True)
    out = resolve_for_insert(db, "potentials", {"potentials_name": "大单"})
    assert out == 50
    assert db.claim_checks == []  # 没跑占用检查


def test_resolve_for_insert_blank_name_still_none():
    """名字为空 → None(沿用既有行为,守卫不改变它)。"""
    from hermes.crm.memory_identity import resolve_for_insert
    db = _FakeIdentityDb()
    assert resolve_for_insert(db, "contacts", {"contacts_name": "  "}) is None


# --------------------------------------------------------------------------
# Task 2 — executor 实体链接（monkeypatch,无 DB）
# --------------------------------------------------------------------------

def test_executor_links_entity_on_commit(monkeypatch):
    from hermes.action_gateway.executors import memory_write as mw
    from hermes.memory.vector_store import UpsertResult

    monkeypatch.setattr(
        mw, "upsert_memory",
        lambda **kw: UpsertResult(committed=True, reason=None, memory_id=555),
    )
    monkeypatch.setattr(mw, "_hash_memory_row", lambda mid, db: (None, None))
    links: list = []
    monkeypatch.setattr(mw, "link_memory_to_entity", lambda **kw: links.append(kw))

    action = {
        "id": "a1",
        "params": {
            "content": "蚂蚁集团整改基本完成于2023年。",
            "link_entity_id": 42, "link_role": "subject",
        },
    }
    token = mw.execute(action, MagicMock())
    assert token["revert_token"] == {"op": "delete", "memory_id": 555}
    assert links == [
        {"entity_id": 42, "memory_id": 555, "role": "subject", "db": ANY}
    ]


def test_executor_links_entity_on_dedupe(monkeypatch):
    from hermes.action_gateway.executors import memory_write as mw
    from hermes.memory.vector_store import UpsertResult

    monkeypatch.setattr(
        mw, "upsert_memory",
        lambda **kw: UpsertResult(committed=False, reason="dedupe", memory_id=777),
    )
    links: list = []
    monkeypatch.setattr(mw, "link_memory_to_entity", lambda **kw: links.append(kw))

    action = {
        "id": "a1",
        "params": {
            "content": "X 是一家 SaaS 公司。",
            "link_entity_id": 9, "link_role": "subject",
        },
    }
    token = mw.execute(action, MagicMock())
    assert token["revert_token"]["op"] == "noop"
    assert links and links[0]["memory_id"] == 777


def test_executor_no_link_without_entity_id(monkeypatch):
    from hermes.action_gateway.executors import memory_write as mw
    from hermes.memory.vector_store import UpsertResult

    monkeypatch.setattr(
        mw, "upsert_memory",
        lambda **kw: UpsertResult(committed=True, reason=None, memory_id=1),
    )
    monkeypatch.setattr(mw, "_hash_memory_row", lambda mid, db: (None, None))
    called: list = []
    monkeypatch.setattr(mw, "link_memory_to_entity", lambda **kw: called.append(kw))

    action = {"id": "a1", "params": {"content": "一条纯散文事实。"}}
    mw.execute(action, MagicMock())
    assert called == []


# --------------------------------------------------------------------------
# Task 3 — ToolSpec
# --------------------------------------------------------------------------

def test_memory_write_toolspec_shape():
    from hermes.tools.crm.dispatch import _memory_write_tool
    spec = _memory_write_tool()
    assert spec.name == "crm_memory_write"
    assert spec.verb == "create"
    props = spec.input_schema["properties"]
    assert props["content"]["type"] == "string"
    assert props["kind"]["enum"] == ["fact", "summary"]
    assert props["related_module"]["enum"] == ["accounts", "contacts", "leads"]
    assert spec.input_schema["required"] == ["content"]
    assert spec.input_schema["additionalProperties"] is False


# --------------------------------------------------------------------------
# Task 4 — _dispatch_memory_write 纯校验（提前返回,不触 DB）
# --------------------------------------------------------------------------

def test_dispatch_memory_write_empty_content():
    from hermes.tools.crm.dispatch import _dispatch_memory_write
    out = _dispatch_memory_write(
        {"content": "  "}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=None,
    )
    assert out == {"success": False, "error": "memory_write_content_required"}


def test_dispatch_memory_write_unpaired_related():
    from hermes.tools.crm.dispatch import _dispatch_memory_write
    out = _dispatch_memory_write(
        {"content": "某公司主营 SaaS。", "related_module": "accounts"},
        user_id=1, source="agent:x", profile_id="p", agent_name="a", db=None,
    )
    assert out == {"success": False, "error": "memory_write_related_pair_required"}


def test_dispatch_memory_write_unsupported_related_module():
    from hermes.tools.crm.dispatch import _dispatch_memory_write
    out = _dispatch_memory_write(
        {"content": "某商机相关事实。", "related_module": "potentials",
         "related_record_id": 5},
        user_id=1, source="agent:x", profile_id="p", agent_name="a", db=None,
    )
    assert out == {
        "success": False, "error": "memory_write_related_module_unsupported",
    }


def test_dispatch_memory_write_soft_capped(monkeypatch):
    """Gateway noop with reason=soft_cap → soft_capped flag, not deduped."""
    from hermes.action_gateway import dispatcher as gw
    from hermes.tools.crm.dispatch import _dispatch_memory_write

    monkeypatch.setattr(
        gw, "submit",
        lambda **kw: {
            "state": "executed", "id": "act-cap-1",
            "revert_token": {"op": "noop", "reason": "soft_cap",
                             "memory_id": None},
        },
    )
    out = _dispatch_memory_write(
        {"content": "配额已满时写入的事实。"},
        user_id=1, source="agent:x", profile_id="p",
        agent_name="a", db=MagicMock(),
    )
    assert out["success"] is True
    assert out.get("soft_capped") is True
    assert "deduped" not in out


def test_dispatch_memory_write_pending_approval(monkeypatch):
    """Gateway returns pending_approval → success=False + pending flag, no retry."""
    from hermes.action_gateway import dispatcher as gw
    from hermes.tools.crm.dispatch import _dispatch_memory_write

    monkeypatch.setattr(
        gw, "submit",
        lambda **kw: {"state": "pending_approval", "id": "act-pending-1",
                      "summary": "需人工审批"},
    )
    out = _dispatch_memory_write(
        {"content": "某客户最近完成 B 轮融资。"},
        user_id=1, source="agent:x", profile_id="p",
        agent_name="a", db=MagicMock(),
    )
    assert out["success"] is False
    assert out["pending_approval"] is True
    assert out["action_id"] == "act-pending-1"
    assert "summary" in out and "message" in out


# --------------------------------------------------------------------------
# Task 4 — _dispatch_memory_write 全流程（真实 PG）
# --------------------------------------------------------------------------

def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


_pg = pytest.mark.skipif(not _crm_reachable(), reason="hermes_crm_dev not reachable")


@pytest.fixture(scope="module")
def profile_id() -> str:
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone("SELECT id FROM profiles LIMIT 1")
    assert row, "profiles table empty — run seed"
    return str(row["id"])


@pytest.fixture(scope="module")
def admin_id() -> int:
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row, "admin user missing — run seed"
    return int(row["id"])


def _cleanup_action(action_id: str) -> None:
    from hermes.crm.db import db_session
    with db_session() as db:
        db.execute("DELETE FROM trust_decisions WHERE action_id = %s", (action_id,))
        db.execute("DELETE FROM gateway_actions WHERE id = %s", (action_id,))


def _cleanup_memory(memory_id: int) -> None:
    from hermes.crm.db import db_session
    with db_session() as db:
        # memory_entity_sources cascades on memories delete.
        db.execute("DELETE FROM memories WHERE id = %s", (int(memory_id),))


@pytest.fixture
def account_with_entity(admin_id):
    """A live accounts row already linked to an org memory_entity."""
    from hermes.crm.db import db_session
    from hermes.memory.entities import upsert_entity
    tag = secrets.token_hex(4)
    with db_session() as db:
        eid = upsert_entity(
            entity_type="org", canonical=f"MWtest-org-{tag}",
            user_id=None, attributes={"source": "test"}, db=db,
        )
        row = db.fetchone(
            "INSERT INTO accounts (accounts_name, owner_by, memory_entity_id) "
            "VALUES (%s, %s, %s) RETURNING accounts_id",
            (f"MWtest-acct-{tag}", admin_id, int(eid)),
        )
        rid = int(row["accounts_id"])
    yield {"record_id": rid, "entity_id": int(eid)}
    with db_session() as db:
        db.execute("DELETE FROM accounts WHERE accounts_id = %s", (rid,))
        db.execute("DELETE FROM memory_entities WHERE id = %s", (int(eid),))


@pytest.fixture
def account_no_entity(admin_id):
    """A live accounts row with NULL memory_entity_id."""
    from hermes.crm.db import db_session
    tag = secrets.token_hex(4)
    with db_session() as db:
        row = db.fetchone(
            "INSERT INTO accounts (accounts_name, owner_by) "
            "VALUES (%s, %s) RETURNING accounts_id",
            (f"MWtest-noent-{tag}", admin_id),
        )
        rid = int(row["accounts_id"])
    yield rid
    with db_session() as db:
        db.execute("DELETE FROM accounts WHERE accounts_id = %s", (rid,))


@_pg
def test_dispatch_writes_prose_fact(profile_id, admin_id):
    import hermes_cloud.app.gateway  # noqa: F401 — register executors
    from hermes.tools.crm.dispatch import dispatch
    out = dispatch(
        tool_name="crm_memory_write",
        arguments={"content": f"测试事实 {secrets.token_hex(6)} —— 客户主营工业软件。"},
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    try:
        assert out["success"] is True
        assert out["memory_id"] > 0
        assert out["linked"] is False
    finally:
        if out.get("memory_id"):
            _cleanup_memory(out["memory_id"])
        if out.get("action_id"):
            _cleanup_action(out["action_id"])


@_pg
def test_dispatch_rejects_serialized_payload(profile_id, admin_id):
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.tools.crm.dispatch import dispatch
    out = dispatch(
        tool_name="crm_memory_write",
        arguments={"content": "{'final_response': None, 'messages': [{'role': 'user'}]}"},
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    assert out["success"] is False
    assert "memory_write_dispatch_failed" in out["error"]


@_pg
def test_dispatch_links_to_account(profile_id, admin_id, account_with_entity):
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.crm.db import db_session
    from hermes.tools.crm.dispatch import dispatch
    out = dispatch(
        tool_name="crm_memory_write",
        arguments={
            "content": f"链接测试事实 {secrets.token_hex(6)}。",
            "related_module": "accounts",
            "related_record_id": account_with_entity["record_id"],
        },
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    try:
        assert out["success"] is True
        assert out["linked"] is True
        with db_session() as db:
            link = db.fetchone(
                "SELECT role FROM memory_entity_sources "
                "WHERE entity_id = %s AND memory_id = %s",
                (account_with_entity["entity_id"], out["memory_id"]),
            )
        assert link is not None
        assert link["role"] == "subject"
    finally:
        if out.get("memory_id"):
            _cleanup_memory(out["memory_id"])
        if out.get("action_id"):
            _cleanup_action(out["action_id"])


@_pg
def test_dispatch_record_not_found(profile_id, admin_id):
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.tools.crm.dispatch import dispatch
    out = dispatch(
        tool_name="crm_memory_write",
        arguments={
            "content": "不该被写入。",
            "related_module": "accounts",
            "related_record_id": 999_999_999,
        },
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    assert out == {"success": False, "error": "memory_write_record_not_found"}


@_pg
def test_dispatch_record_has_no_entity(profile_id, admin_id, account_no_entity):
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.tools.crm.dispatch import dispatch
    out = dispatch(
        tool_name="crm_memory_write",
        arguments={
            "content": f"无实体记录的事实 {secrets.token_hex(6)}。",
            "related_module": "accounts",
            "related_record_id": account_no_entity,
        },
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    try:
        assert out["success"] is True
        assert out["linked"] is False
        assert out["reason"] == "record_has_no_entity"
    finally:
        if out.get("memory_id"):
            _cleanup_memory(out["memory_id"])
        if out.get("action_id"):
            _cleanup_action(out["action_id"])


@_pg
def test_dispatch_dedupe_still_links(profile_id, admin_id, account_with_entity):
    """同一 content 二次写:不新建 memories 行,但仍补链接到第二个实体。"""
    import hermes_cloud.app.gateway  # noqa: F401
    from hermes.crm.db import db_session
    from hermes.memory.entities import upsert_entity
    from hermes.tools.crm.dispatch import dispatch

    shared = f"去重测试事实 {secrets.token_hex(6)}。"
    out1 = dispatch(
        tool_name="crm_memory_write",
        arguments={
            "content": shared, "related_module": "accounts",
            "related_record_id": account_with_entity["record_id"],
        },
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    out2: dict = {}
    eid2 = None
    rid2 = None
    try:
        tag = secrets.token_hex(4)
        with db_session() as db:
            eid2 = upsert_entity(
                entity_type="org", canonical=f"MWtest-org2-{tag}",
                user_id=None, attributes={"source": "test"}, db=db,
            )
            row = db.fetchone(
                "INSERT INTO accounts (accounts_name, owner_by, memory_entity_id) "
                "VALUES (%s, %s, %s) RETURNING accounts_id",
                (f"MWtest-acct2-{tag}", admin_id, int(eid2)),
            )
            rid2 = int(row["accounts_id"])
        out2 = dispatch(
            tool_name="crm_memory_write",
            arguments={
                "content": shared, "related_module": "accounts",
                "related_record_id": rid2,
            },
            user_id=admin_id, source="agent:test",
            profile_id=profile_id, agent_name="test-agent",
        )
        assert out2["success"] is True
        assert out2.get("deduped") is True
        assert out2["memory_id"] == out1["memory_id"]
        assert out2["linked"] is True
        with db_session() as db:
            link = db.fetchone(
                "SELECT 1 FROM memory_entity_sources "
                "WHERE entity_id = %s AND memory_id = %s",
                (int(eid2), out2["memory_id"]),
            )
        assert link is not None
    finally:
        if out1.get("memory_id"):
            _cleanup_memory(out1["memory_id"])
        for o in (out1, out2):
            if o and o.get("action_id"):
                _cleanup_action(o["action_id"])
        with db_session() as db:
            if rid2 is not None:
                db.execute("DELETE FROM accounts WHERE accounts_id = %s", (rid2,))
            if eid2 is not None:
                db.execute("DELETE FROM memory_entities WHERE id = %s", (int(eid2),))


# --------------------------------------------------------------------------
# soft-cap 回归 — 配额耗尽时不得误报 linked
# --------------------------------------------------------------------------

def test_dispatch_soft_cap_reports_not_linked(monkeypatch):
    """配额 soft-cap 时什么都没写、也没链接 —— linked 必须为 False,
    即便调用方给了 related_module/record_id 且解析到了实体。"""
    from hermes.tools.crm import dispatch as d

    monkeypatch.setattr(
        "hermes.crm.memory_identity.resolve_record_entity",
        lambda db, table, rid: (True, 42),
    )
    monkeypatch.setattr(
        "hermes.action_gateway.dispatcher.submit",
        lambda **kw: {
            "id": "act-softcap",
            "state": "executed",
            "revert_token": {"op": "noop", "reason": "soft_cap", "memory_id": None},
        },
    )
    out = d._dispatch_memory_write(
        {
            "content": "某客户预算约 50 万。",
            "related_module": "accounts",
            "related_record_id": 7,
        },
        user_id=1, source="agent:test", profile_id="p",
        agent_name="a", db=MagicMock(),
    )
    assert out["success"] is True
    assert out["soft_capped"] is True
    assert out["linked"] is False
