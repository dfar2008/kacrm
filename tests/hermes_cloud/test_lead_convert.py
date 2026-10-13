"""crm_convert_lead 工具 + lead.convert executor 测试。"""
from __future__ import annotations

import secrets
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Task 2 — executor 纯逻辑(monkeypatch Save / ModulesDb,无真实 DB)
# ---------------------------------------------------------------------------

def _fake_lead_row(**over):
    row = {
        "leads_id": 10, "leads_name": "测试线索公司", "contact": "王经理",
        "phone": "020-1", "mobile": "13700000000", "email": "w@x.com",
        "owner_by": 3, "convertedstatus": "pending",
        "convertedby": None, "converteddate": None,
    }
    row.update(over)
    return row


def _patch_executor(monkeypatch, lead_row):
    """把 lead.convert executor 的 DB 依赖换成可控 fake。返回 inserts 记录表。"""
    from hermes.action_gateway.executors import lead_convert as lc

    inserts: list = []
    updates: list = []

    class _FakeSave:
        def __init__(self, db): pass
        def set_module(self, mid): self._mid = mid; return self
        def set_user(self, uid, is_admin=False): return self
        def set_source(self, src): return self
        def insert(self, data):
            inserts.append((self._mid, data))
            return {1: 101, 2: 202, 3: 303}[self._mid]  # acc/con/pot module ids

    class _FakeModulesDb:
        def __init__(self, db): pass
        def get_module_by_name(self, name):
            return {"accounts": {"id": 1}, "contacts": {"id": 2},
                    "potentials": {"id": 3}}[name]

    db = MagicMock()
    db.fetchone.return_value = lead_row
    db.execute.side_effect = lambda sql, args=None: updates.append((sql, args))

    monkeypatch.setattr(lc, "Save", _FakeSave)
    monkeypatch.setattr(lc, "ModulesDb", _FakeModulesDb)
    monkeypatch.setattr(lc, "_hash_account", lambda aid, db: (None, None))
    return db, inserts, updates


def test_executor_creates_three_records_and_flips_lead(monkeypatch):
    from hermes.action_gateway.executors import lead_convert as lc
    db, inserts, updates = _patch_executor(monkeypatch, _fake_lead_row())

    action = {
        "id": "act-1", "user_id": 3, "profile_id": "p1",
        "agent_name": "crm-intake",
        "params": {"lead_id": 10},
    }
    out = lc.execute(action, db)

    # 3 条 insert:account / contact / potential
    assert [mid for mid, _ in inserts] == [1, 2, 3]
    acc_data = inserts[0][1]
    assert acc_data["accounts_name"] == "测试线索公司"
    assert acc_data["leads_id"] == 10
    con_data = inserts[1][1]
    assert con_data["contacts_name"] == "王经理"
    assert con_data["accounts_id"] == 101
    pot_data = inserts[2][1]
    assert pot_data["accounts_id"] == 101 and pot_data["contacts_id"] == 202
    # lead 被翻成已转化
    assert any("convertedstatus" in (sql or "") for sql, _ in updates)
    # revert token 形状
    tok = out["revert_token"]
    assert tok == {
        "op": "lead_convert", "account_id": 101, "contact_id": 202,
        "potential_id": 303, "lead_id": 10,
        "prior_convertedstatus": "pending",
        "prior_convertedby": None, "prior_converteddate": None,
    }


def test_executor_contact_name_falls_back_to_lead_name(monkeypatch):
    from hermes.action_gateway.executors import lead_convert as lc
    db, inserts, _ = _patch_executor(
        monkeypatch, _fake_lead_row(contact=""))
    lc.execute({"id": "a", "user_id": 3, "params": {"lead_id": 10}}, db)
    assert inserts[1][1]["contacts_name"] == "测试线索公司"


def test_executor_rejects_already_converted(monkeypatch):
    from hermes.action_gateway.executors import lead_convert as lc
    db, _, _ = _patch_executor(
        monkeypatch, _fake_lead_row(convertedstatus="converted"))
    with pytest.raises(ValueError, match="already converted"):
        lc.execute({"id": "a", "user_id": 3, "params": {"lead_id": 10}}, db)


def test_executor_rejects_missing_lead(monkeypatch):
    from hermes.action_gateway.executors import lead_convert as lc
    db, _, _ = _patch_executor(monkeypatch, None)
    with pytest.raises(ValueError, match="not found"):
        lc.execute({"id": "a", "user_id": 3, "params": {"lead_id": 99}}, db)


def test_executor_potential_name_default_and_override(monkeypatch):
    from hermes.action_gateway.executors import lead_convert as lc
    # 缺省名
    db, inserts, _ = _patch_executor(monkeypatch, _fake_lead_row())
    lc.execute({"id": "a", "user_id": 3, "params": {"lead_id": 10}}, db)
    assert inserts[2][1]["potentials_name"] == "测试线索公司 商机"
    # 入参覆盖
    db2, inserts2, _ = _patch_executor(monkeypatch, _fake_lead_row())
    lc.execute({"id": "a", "user_id": 3,
                "params": {"lead_id": 10, "potential_name": "大单"}}, db2)
    assert inserts2[2][1]["potentials_name"] == "大单"


def test_revert_soft_deletes_and_restores(monkeypatch):
    from hermes.action_gateway.executors import lead_convert as lc
    calls: list = []
    db = MagicMock()
    db.execute.side_effect = lambda sql, args=None: calls.append((sql, args))
    monkeypatch.setattr(lc, "_hash_account", lambda aid, db: (None, None))

    token = {
        "op": "lead_convert", "account_id": 101, "contact_id": 202,
        "potential_id": 303, "lead_id": 10,
        "prior_convertedstatus": "pending",
        "prior_convertedby": None, "prior_converteddate": None,
    }
    lc.revert(token, db)
    joined = " ".join(sql for sql, _ in calls)
    assert "accounts" in joined and "contacts" in joined and "potentials" in joined
    assert "deleted = TRUE" in joined
    assert "leads_id = NULL" in joined
    assert "UPDATE leads" in joined


# ---------------------------------------------------------------------------
# Task 3 — ToolSpec
# ---------------------------------------------------------------------------

def test_convert_lead_toolspec_shape():
    from hermes.tools.crm.dispatch import _convert_lead_tool
    spec = _convert_lead_tool()
    assert spec.name == "crm_convert_lead"
    assert spec.verb == "create"
    props = spec.input_schema["properties"]
    assert props["lead_id"]["type"] == "integer"
    assert spec.input_schema["required"] == ["lead_id"]
    assert spec.input_schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Task 3 — _dispatch_convert_lead 纯校验(提前返回,不触 DB)
# ---------------------------------------------------------------------------

def test_dispatch_convert_lead_missing_id():
    from hermes.tools.crm.dispatch import _dispatch_convert_lead
    out = _dispatch_convert_lead(
        {}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=None,
    )
    assert out == {"success": False, "error": "convert_lead_id_required"}


def test_dispatch_convert_lead_bad_id():
    from hermes.tools.crm.dispatch import _dispatch_convert_lead
    out = _dispatch_convert_lead(
        {"lead_id": 0}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=None,
    )
    assert out == {"success": False, "error": "convert_lead_id_required"}


def test_dispatch_convert_lead_not_found(monkeypatch):
    from unittest.mock import MagicMock
    from hermes.tools.crm.dispatch import _dispatch_convert_lead
    db = MagicMock()
    db.fetchone.return_value = None
    out = _dispatch_convert_lead(
        {"lead_id": 999}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=db,
    )
    assert out == {"success": False, "error": "convert_lead_not_found"}


def test_dispatch_convert_lead_already_converted(monkeypatch):
    from unittest.mock import MagicMock
    from hermes.tools.crm.dispatch import _dispatch_convert_lead
    db = MagicMock()
    db.fetchone.return_value = {"leads_id": 5, "convertedstatus": "converted"}
    out = _dispatch_convert_lead(
        {"lead_id": 5}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=db,
    )
    assert out == {"success": False, "error": "convert_lead_already_converted"}


def test_dispatch_convert_lead_executed(monkeypatch):
    """gateway 返回 executed → success=True + 三个新记录 id。"""
    from unittest.mock import MagicMock
    from hermes.action_gateway import dispatcher as gw
    from hermes.tools.crm.dispatch import _dispatch_convert_lead

    db = MagicMock()
    db.fetchone.return_value = {"leads_id": 5, "convertedstatus": "pending"}
    monkeypatch.setattr(
        gw, "submit",
        lambda **kw: {
            "state": "executed", "id": "act-conv-1",
            "revert_token": {
                "op": "lead_convert", "account_id": 101,
                "contact_id": 202, "potential_id": 303, "lead_id": 5,
            },
        },
    )
    out = _dispatch_convert_lead(
        {"lead_id": 5}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=db,
    )
    assert out["success"] is True
    assert out["account_id"] == 101
    assert out["contact_id"] == 202
    assert out["potential_id"] == 303
    assert out["action_id"] == "act-conv-1"


def test_dispatch_convert_lead_pending(monkeypatch):
    """gateway 返回 pending_approval → success=False + pending 标志,不重试。"""
    from unittest.mock import MagicMock
    from hermes.action_gateway import dispatcher as gw
    from hermes.tools.crm.dispatch import _dispatch_convert_lead

    db = MagicMock()
    db.fetchone.return_value = {"leads_id": 5, "convertedstatus": "pending"}
    monkeypatch.setattr(
        gw, "submit",
        lambda **kw: {"state": "pending_approval", "id": "act-conv-2",
                      "summary": "lead 转化待审批"},
    )
    out = _dispatch_convert_lead(
        {"lead_id": 5}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=db,
    )
    assert out["success"] is False
    assert out["pending_approval"] is True
    assert out["action_id"] == "act-conv-2"
    assert "message" in out


def test_dispatch_convert_lead_non_integer_id():
    from hermes.tools.crm.dispatch import _dispatch_convert_lead
    out = _dispatch_convert_lead(
        {"lead_id": "abc"}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=None,
    )
    assert out == {"success": False, "error": "convert_lead_id_required"}


def test_dispatch_convert_lead_gateway_exception(monkeypatch):
    """gateway.submit 抛异常 → success=False + convert_lead_dispatch_failed。"""
    from unittest.mock import MagicMock
    from hermes.action_gateway import dispatcher as gw
    from hermes.tools.crm.dispatch import _dispatch_convert_lead

    db = MagicMock()
    db.fetchone.return_value = {"leads_id": 5, "convertedstatus": "pending"}

    def _boom(**kw):
        raise RuntimeError("db gone")
    monkeypatch.setattr(gw, "submit", _boom)
    out = _dispatch_convert_lead(
        {"lead_id": 5}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=db,
    )
    assert out["success"] is False
    assert "convert_lead_dispatch_failed" in out["error"]


def test_dispatch_convert_lead_unexpected_state(monkeypatch):
    """gateway 返回非 executed/pending 状态 → success=False + unexpected_state。"""
    from unittest.mock import MagicMock
    from hermes.action_gateway import dispatcher as gw
    from hermes.tools.crm.dispatch import _dispatch_convert_lead

    db = MagicMock()
    db.fetchone.return_value = {"leads_id": 5, "convertedstatus": "pending"}
    monkeypatch.setattr(
        gw, "submit",
        lambda **kw: {"state": "blocked", "id": "act-conv-3"},
    )
    out = _dispatch_convert_lead(
        {"lead_id": 5}, user_id=1, source="agent:x",
        profile_id="p", agent_name="a", db=db,
    )
    assert out["success"] is False
    assert "convert_lead_unexpected_state" in out["error"]
    assert out["action_id"] == "act-conv-3"


# ---------------------------------------------------------------------------
# Task 4 — 端到端集成(真实 PG;无 PG 自动跳过)
# ---------------------------------------------------------------------------

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
    assert row, "profiles 表为空 — 先 seed"
    return str(row["id"])


@pytest.fixture(scope="module")
def admin_id() -> int:
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1")
    assert row, "admin 用户缺失 — 先 seed"
    return int(row["id"])


@pytest.fixture
def fresh_lead(admin_id):
    """造一条未转化的 lead,测完清理。"""
    from hermes.crm.db import db_session
    tag = secrets.token_hex(4)
    with db_session() as db:
        row = db.fetchone(
            "INSERT INTO leads (leads_name, contact, mobile, owner_by, "
            "convertedstatus) VALUES (%s, %s, %s, %s, 'pending') "
            "RETURNING leads_id",
            (f"LCtest-{tag}", "陈经理", "13900000000", admin_id),
        )
        lead_id = int(row["leads_id"])
    yield lead_id
    with db_session() as db:
        db.execute("DELETE FROM leads WHERE leads_id = %s", (lead_id,))


@_pg
def test_e2e_convert_lead(profile_id, admin_id, fresh_lead):
    import hermes.action_gateway  # noqa: F401 — 注册 executor
    from hermes.crm.db import db_session
    from hermes.tools.crm.dispatch import dispatch

    out = dispatch(
        tool_name="crm_convert_lead",
        arguments={"lead_id": fresh_lead},
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    created = []
    try:
        # 转化成功(或因 trust 门控 pending — 两者都不是 bug)
        if out.get("pending_approval"):
            pytest.skip("trust 门控为 pending_approval — 转化逻辑未触发,跳过断言")
        assert out["success"] is True, out
        acc, con, pot = (out["account_id"], out["contact_id"],
                         out["potential_id"])
        created = [("accounts", "accounts_id", acc),
                   ("contacts", "contacts_id", con),
                   ("potentials", "potentials_id", pot)]
        assert acc > 0 and con > 0 and pot > 0
        with db_session() as db:
            a = db.fetchone(
                "SELECT leads_id FROM accounts WHERE accounts_id = %s", (acc,))
            assert a["leads_id"] == fresh_lead
            c = db.fetchone(
                "SELECT accounts_id FROM contacts WHERE contacts_id = %s", (con,))
            assert c["accounts_id"] == acc
            p = db.fetchone(
                "SELECT accounts_id, contacts_id FROM potentials "
                "WHERE potentials_id = %s", (pot,))
            assert p["accounts_id"] == acc and p["contacts_id"] == con
            lead = db.fetchone(
                "SELECT convertedstatus, convertedby, converteddate "
                "FROM leads WHERE leads_id = %s", (fresh_lead,))
            assert lead["convertedstatus"] == "converted"
            assert lead["convertedby"] == admin_id
            assert lead["converteddate"] is not None
    finally:
        with db_session() as db:
            for table, pk, rid in created:
                db.execute(
                    f"DELETE FROM {table} WHERE {pk} = %s", (rid,))
            if out.get("action_id"):
                db.execute(
                    "DELETE FROM trust_decisions WHERE action_id = %s",
                    (out["action_id"],))
                db.execute(
                    "DELETE FROM gateway_actions WHERE id = %s",
                    (out["action_id"],))


@_pg
def test_e2e_double_convert_rejected(profile_id, admin_id, fresh_lead):
    """已转化的 lead 再次转化 → convert_lead_already_converted。"""
    import hermes.action_gateway  # noqa: F401
    from hermes.crm.db import db_session
    from hermes.tools.crm.dispatch import dispatch

    with db_session() as db:
        db.execute(
            "UPDATE leads SET convertedstatus = 'converted' "
            "WHERE leads_id = %s", (fresh_lead,))
    out = dispatch(
        tool_name="crm_convert_lead",
        arguments={"lead_id": fresh_lead},
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    assert out == {"success": False, "error": "convert_lead_already_converted"}


@pytest.fixture
def lead_with_claimed_contact(admin_id):
    """造 (1) 一个已占用 person 实体的活联系人,(2) 一条 contact 同名的待转化
    lead。复现 uniq_contacts_memory_entity 撞库的前置。测完清理。"""
    from hermes.crm.db import db_session
    from hermes.memory.entities import upsert_entity
    tag = secrets.token_hex(4)
    person_name = f"LCdup-{tag}"
    with db_session() as db:
        eid = int(upsert_entity(
            entity_type="person", canonical=person_name,
            user_id=None, attributes={"source": "test"}, db=db,
        ))
        c = db.fetchone(
            "INSERT INTO contacts (contacts_name, owner_by, is_main, "
            "memory_entity_id) VALUES (%s, %s, TRUE, %s) RETURNING contacts_id",
            (person_name, admin_id, eid),
        )
        existing_contact_id = int(c["contacts_id"])
        l = db.fetchone(
            "INSERT INTO leads (leads_name, contact, mobile, owner_by, "
            "convertedstatus) VALUES (%s, %s, %s, %s, 'pending') "
            "RETURNING leads_id",
            (f"LCcompany-{tag}", person_name, "13900000001", admin_id),
        )
        lead_id = int(l["leads_id"])
    yield {"lead_id": lead_id, "entity_id": eid,
           "existing_contact_id": existing_contact_id, "name": person_name}
    with db_session() as db:
        db.execute("DELETE FROM leads WHERE leads_id = %s", (lead_id,))
        db.execute("DELETE FROM contacts WHERE contacts_id = %s",
                   (existing_contact_id,))
        db.execute("DELETE FROM memory_entities WHERE id = %s", (eid,))


@_pg
def test_e2e_convert_lead_with_claimed_person_entity(
    profile_id, admin_id, lead_with_claimed_contact
):
    """回归:lead 的联系人名已被现有 contact 的 person 实体占用时,转化不得
    撞 uniq_contacts_memory_entity 抛 500;新 contact 的 memory_entity_id
    置 NULL,既有 contact 仍独占实体。"""
    import hermes.action_gateway  # noqa: F401 — 注册 executor
    from hermes.crm.db import db_session
    from hermes.tools.crm.dispatch import dispatch

    fx = lead_with_claimed_contact
    out = dispatch(
        tool_name="crm_convert_lead",
        arguments={"lead_id": fx["lead_id"]},
        user_id=admin_id, source="agent:test",
        profile_id=profile_id, agent_name="test-agent",
    )
    created = []
    try:
        if out.get("pending_approval"):
            pytest.skip("trust 门控为 pending_approval — 转化逻辑未触发,跳过断言")
        # 关键:不再 500 / dispatch_failed,转化成功。
        assert out.get("success") is True, out
        acc, con, pot = (out["account_id"], out["contact_id"],
                         out["potential_id"])
        created = [("accounts", "accounts_id", acc),
                   ("contacts", "contacts_id", con),
                   ("potentials", "potentials_id", pot)]
        with db_session() as db:
            new_c = db.fetchone(
                "SELECT memory_entity_id FROM contacts WHERE contacts_id = %s",
                (con,))
            # 新 contact 让出实体 → NULL(占用守卫生效)。
            assert new_c["memory_entity_id"] is None, new_c
            # 既有 contact 仍独占该 person 实体。
            old_c = db.fetchone(
                "SELECT memory_entity_id FROM contacts WHERE contacts_id = %s",
                (fx["existing_contact_id"],))
            assert old_c["memory_entity_id"] == fx["entity_id"]
    finally:
        with db_session() as db:
            for table, pk, rid in created:
                db.execute(f"DELETE FROM {table} WHERE {pk} = %s", (rid,))
            if out.get("action_id"):
                db.execute("DELETE FROM trust_decisions WHERE action_id = %s",
                           (out["action_id"],))
                db.execute("DELETE FROM gateway_actions WHERE id = %s",
                           (out["action_id"],))


@_pg
def test_e2e_convert_lead_in_catalogue():
    """crm_convert_lead 出现在 build_crm_tools 工具目录里。"""
    from hermes.tools.crm.dispatch import build_crm_tools
    names = {t.name for t in build_crm_tools()}
    assert "crm_convert_lead" in names
