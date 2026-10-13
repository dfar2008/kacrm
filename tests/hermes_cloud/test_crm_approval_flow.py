"""CRM 审批链补全 — 引擎/守卫/详情真库测试。

连真 dev 库 hermes_crm_dev(同现有 CRM 测试策略)。需 HERMES_TESTS_REAL_PG=1
解除 psycopg stub,否则库不可达整体 skip。salesorders 模块在真库已 seed(id=6)。
"""
from __future__ import annotations

import pytest

from hermes.crm.approval.engine import ApprovalEngine
from hermes.tools.crm.dispatch import build_crm_tools, dispatch


def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.requires_real_pg,
    pytest.mark.skipif(not _crm_reachable(), reason="hermes_crm_dev not reachable"),
]

_SO = "salesorders"  # 真库已 seed 的模块,走审批


@pytest.fixture
def db():
    from hermes.crm.db import db_session

    with db_session() as conn:
        yield conn


@pytest.fixture
def mod_id(db) -> int:
    return int(db.fetchone("SELECT id FROM modules WHERE name=%s", (_SO,))["id"])


@pytest.fixture(autouse=True)
def _clean_approvals(db, mod_id):
    """每个测试前后清掉本模块的审批配置与实例,避免真库污染。"""

    def _wipe():
        db.execute(
            "DELETE FROM approval_steps WHERE approval_id IN "
            "(SELECT id FROM approvals WHERE module_id=%s)",
            (mod_id,),
        )
        db.execute("DELETE FROM approvals WHERE module_id=%s", (mod_id,))
        db.execute("DELETE FROM approval_configs WHERE module_id=%s", (mod_id,))

    _wipe()
    yield
    _wipe()


_STEPS = [{"step_name": "经理", "approver_ids": [7]}]


def test_submit_skips_when_no_config(db, mod_id):
    eng = ApprovalEngine(db)
    res = eng.submit_for_record(mod_id, data_id=999001, user_id=7)
    assert res == {"skipped": "no_config"}


def test_submit_creates_chain_when_config_present(db, mod_id):
    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    res = eng.submit_for_record(mod_id, data_id=999001, user_id=3)
    assert res["submitted"] is True
    assert isinstance(res["approval_id"], int)
    assert eng.has_pending(mod_id, 999001) is True


def test_submit_skips_when_already_pending(db, mod_id):
    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    eng.submit_for_record(mod_id, 999001, 3)
    res = eng.submit_for_record(mod_id, 999001, 3)
    assert res == {"skipped": "already_pending"}


def test_has_passed_true_after_single_step_approved(db, mod_id):
    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    res = eng.submit_for_record(mod_id, 999001, 3)
    eng.approve(res["approval_id"], 7)
    assert eng.has_passed(mod_id, 999001) is True


# ---- A2: crm_submit_approval agent 工具 ----


def test_submit_approval_tool_in_agent_catalogue(db):
    names = {t.name for t in build_crm_tools(db)}
    assert "crm_submit_approval" in names


def test_approval_action_tools_in_agent_catalogue(db):
    # agent 不仅能提交,也能列待审批 + 同意/驳回(审批侧工具进 catalogue)。
    names = {t.name for t in build_crm_tools(db)}
    assert {"crm_pending_approval_chains", "crm_approve_chain_step",
            "crm_reject_chain_step"} <= names


def test_agent_full_submit_then_approve_via_dispatch(db, mod_id):
    # admin 提交 → 王钊(审批人)列待审批 → 同意 → passed,全走 dispatch。
    eng = ApprovalEngine(db)
    eng.save_config(mod_id, [{"step_name": "经理审批", "approver_ids": [3132]}])
    sub = dispatch(tool_name="crm_submit_approval",
                   arguments={"module": _SO, "record_id": 999400}, user_id=1, db=db)
    assert sub["submitted"] is True
    pend = dispatch(tool_name="crm_pending_approval_chains",
                    arguments={}, user_id=3132, db=db)
    assert any(int(a["record_id"]) == 999400 for a in pend["approvals"])
    appr = dispatch(tool_name="crm_approve_chain_step",
                    arguments={"approval_id": sub["approval_id"]}, user_id=3132, db=db)
    assert appr["result"]["status"] == "passed"


def test_dispatch_submit_approval_creates_chain(db, mod_id):
    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    res = dispatch(
        tool_name="crm_submit_approval",
        arguments={"module": _SO, "record_id": 999002},
        user_id=3,
        db=db,
    )
    assert res["success"] is True
    assert res["submitted"] is True
    assert eng.has_pending(mod_id, 999002) is True


def test_dispatch_submit_approval_skips_no_config(db, mod_id):
    res = dispatch(
        tool_name="crm_submit_approval",
        arguments={"module": _SO, "record_id": 999003},
        user_id=3,
        db=db,
    )
    assert res.get("skipped") == "no_config"


# ---- B1: 写锁 guard ----


def test_guard_passes_when_no_config(db, mod_id):
    from hermes.crm.approval.guard import approval_guard

    approval_guard(db, mod_id, 999100, source="user")  # 未配链 → 不抛


def test_guard_blocks_when_pending(db, mod_id):
    from hermes.crm.approval.guard import approval_guard, ApprovalLockedError

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    eng.submit_for_record(mod_id, 999100, 3)
    with pytest.raises(ApprovalLockedError):
        approval_guard(db, mod_id, 999100, source="user")


def test_guard_bypassed_for_internal_source(db, mod_id):
    from hermes.crm.approval.guard import approval_guard

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    eng.submit_for_record(mod_id, 999100, 3)
    approval_guard(db, mod_id, 999100, source="approval")  # 内部流转 → 不抛


def test_guard_blocks_after_passed_when_edit_disallowed(db, mod_id):
    from hermes.crm.approval.guard import approval_guard, ApprovalLockedError

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS, 0)
    res = eng.submit_for_record(mod_id, 999100, 3)
    eng.approve(res["approval_id"], 7)
    with pytest.raises(ApprovalLockedError):
        approval_guard(db, mod_id, 999100, source="user")


def test_guard_allows_after_passed_when_edit_allowed(db, mod_id):
    from hermes.crm.approval.guard import approval_guard

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS, 1)
    res = eng.submit_for_record(mod_id, 999100, 3)
    eng.approve(res["approval_id"], 7)
    approval_guard(db, mod_id, 999100, source="user")  # 允许改 → 不抛


# ---- B2/B3: save.update / item_delete 接入写锁 ----
# guard 只用 module_id/record_id/source/db,pending 态可直接触发,无需造真记录。


def test_save_update_blocked_when_pending(db, mod_id):
    from hermes.crm.save import Save
    from hermes.crm.approval.guard import ApprovalLockedError

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    eng.submit_for_record(mod_id, 999200, 3)
    with pytest.raises(ApprovalLockedError):
        Save(db).set_module(mod_id).set_record_id(999200).set_user(3, True).update(
            {"salesorders_name": "x"}
        )


def test_delete_business_rules_block_when_pending(db, mod_id):
    from hermes.crm.item_delete import ItemDelete
    from hermes.crm.approval.guard import ApprovalLockedError

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    eng.submit_for_record(mod_id, 999200, 3)
    d = ItemDelete(db).set_params(mod_id, 999200, 3)
    with pytest.raises(ApprovalLockedError):
        d._check_business_rules()


# ---- C: detail_query 填充 has_approval / approval ----


def test_detail_query_reports_approval(db, mod_id):
    from hermes.crm.detail_query import DetailQuery

    rid = int(
        db.fetchone(
            "INSERT INTO salesorders (salesorders_name, final_amount, deleted) "
            "VALUES (%s, %s, FALSE) RETURNING salesorders_id",
            ("审批测试单", 0),
        )["salesorders_id"]
    )
    try:
        eng = ApprovalEngine(db)
        eng.save_config(mod_id, _STEPS)
        eng.submit_for_record(mod_id, rid, 3)
        out = (
            DetailQuery(db)
            .set_module(mod_id)
            .set_record_id(rid)
            .set_user(3, True)
            .execute()
        )
        assert out["has_approval"] is True
        assert out["approval"]["approval"]["status"] == "pending"
        assert len(out["approval"]["steps"]) == 1
    finally:
        db.execute("DELETE FROM salesorders WHERE salesorders_id = %s", (rid,))


def test_detail_query_no_approval_when_unconfigured(db, mod_id):
    from hermes.crm.detail_query import DetailQuery

    rid = int(
        db.fetchone(
            "INSERT INTO salesorders (salesorders_name, final_amount, deleted) "
            "VALUES (%s, %s, FALSE) RETURNING salesorders_id",
            ("无审批单", 0),
        )["salesorders_id"]
    )
    try:
        out = (
            DetailQuery(db)
            .set_module(mod_id)
            .set_record_id(rid)
            .set_user(3, True)
            .execute()
        )
        assert out["has_approval"] is False
        assert out["approval"] == {}
    finally:
        db.execute("DELETE FROM salesorders WHERE salesorders_id = %s", (rid,))


# ---- E: workflow submit_approval 动作 ----


def test_workflow_submit_approval_action(db, mod_id):
    from hermes_cloud.app.crm.workflows import _execute_engine_action

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, _STEPS)
    wf = {"module": _SO, "name": "wf", "_trigger": "add"}
    res = _execute_engine_action(
        db, {"kind": "submit_approval", "params": {}}, wf, {"salesorders_id": 999300}, 3
    )
    assert res["ok"] is True
    assert res["undo"]["type"] == "cancel_approval"
    assert eng.has_pending(mod_id, 999300) is True


def test_workflow_submit_approval_skips_no_config(db, mod_id):
    from hermes_cloud.app.crm.workflows import _execute_engine_action

    wf = {"module": _SO, "name": "wf", "_trigger": "add"}
    res = _execute_engine_action(
        db, {"kind": "submit_approval", "params": {}}, wf, {"salesorders_id": 999301}, 3
    )
    assert res["ok"] is True
    assert res["skipped"] == "no_config"
    assert res["undo"] is None


def test_writeback_status_to_record(db, mod_id):
    """提交/通过/拒绝/撤销 → 业务记录 approval_status/审批人/时间 同步。"""
    eng = ApprovalEngine(db)
    eng.save_config(mod_id, [{"step_name": "经理审批", "approver_ids": [3132]}])

    def _mk():
        return int(db.fetchone(
            "INSERT INTO salesorders (salesorders_name, final_amount, deleted) "
            "VALUES ('回写测试', 1000, FALSE) RETURNING salesorders_id"
        )["salesorders_id"])

    def _rec(rid):
        return db.fetchone(
            "SELECT approval_status, approval_user_id, approval_time "
            "FROM salesorders WHERE salesorders_id=%s", (rid,)
        )

    rids = []
    try:
        # 提交 → pending,审批人/时间空
        r1 = _mk(); rids.append(r1)
        aid = eng.submit_for_record(mod_id, r1, 1)["approval_id"]
        rec = _rec(r1)
        assert rec["approval_status"] == "pending"
        assert rec["approval_user_id"] is None and rec["approval_time"] is None

        # 通过 → approved + 审批人=3132 + 时间非空
        eng.approve(aid, 3132)
        rec = _rec(r1)
        assert rec["approval_status"] == "approved"
        assert rec["approval_user_id"] == 3132 and rec["approval_time"] is not None

        # 另一条:拒绝 → rejected + 审批人
        r2 = _mk(); rids.append(r2)
        aid2 = eng.submit_for_record(mod_id, r2, 1)["approval_id"]
        eng.reject(aid2, 3132, "金额过高")
        rec = _rec(r2)
        assert rec["approval_status"] == "rejected"
        assert rec["approval_user_id"] == 3132

        # 再一条:撤销 → 三字段清空
        r3 = _mk(); rids.append(r3)
        aid3 = eng.submit_for_record(mod_id, r3, 1)["approval_id"]
        assert _rec(r3)["approval_status"] == "pending"
        eng.cancel(aid3)
        rec = _rec(r3)
        assert rec["approval_status"] is None
        assert rec["approval_user_id"] is None and rec["approval_time"] is None
    finally:
        for rid in rids:
            db.execute("DELETE FROM salesorders WHERE salesorders_id=%s", (rid,))


def test_approval_logs_append_only(db, mod_id):
    """每次 提交/通过/拒绝/退回 都往 approval_logs 追加,get_detail 返回带姓名。"""
    eng = ApprovalEngine(db)
    # 两步链:第1步王钊,第2步王钊(便于单人跑完)
    eng.save_config(mod_id, [
        {"step_name": "一审", "approver_ids": [3132]},
        {"step_name": "二审", "approver_ids": [3132]},
    ])
    rid = int(db.fetchone(
        "INSERT INTO salesorders (salesorders_name, final_amount, deleted) "
        "VALUES ('日志测试', 5000, FALSE) RETURNING salesorders_id"
    )["salesorders_id"])
    try:
        db.execute("DELETE FROM approval_logs WHERE data_id=%s", (rid,))
        aid = eng.submit_for_record(mod_id, rid, 3132)["approval_id"]
        eng.approve(aid, 3132)          # 一审通过 → 进二审
        eng.back_to_step(aid, 3132, 1)  # 二审退回到一审
        eng.approve(aid, 3132)          # 一审再过 → 进二审
        eng.approve(aid, 3132)          # 二审过 → 终审通过

        detail = eng.get_detail(aid)
        logs = detail["logs"]
        actions = [l["action"] for l in logs]
        # submit + approve + back + approve + approve = 5 条,顺序保留
        assert actions == ["submit", "approve", "back", "approve", "approve"], actions
        # 退回那条没被后续覆盖(append-only 的关键)
        assert any(l["action"] == "back" and "退回至第1步" in (l["remark"] or "") for l in logs)
        # 操作人解析出姓名
        assert all(l["actor_name"] == "王钊" for l in logs if l["actor_id"])
    finally:
        db.execute("DELETE FROM approval_logs WHERE data_id=%s", (rid,))
        db.execute("DELETE FROM salesorders WHERE salesorders_id=%s", (rid,))


def test_get_detail_resolves_step_approver_names(db, mod_id):
    """get_detail 给每步解析 approver_names(配置审批人姓名,供下一步显示)。"""
    eng = ApprovalEngine(db)
    eng.save_config(mod_id, [
        {"step_name": "一审", "approver_ids": [3132]},
        {"step_name": "二审", "approver_ids": [3132]},
    ])
    rid = int(db.fetchone(
        "INSERT INTO salesorders (salesorders_name, final_amount, deleted) "
        "VALUES ('审批人显示', 9000, FALSE) RETURNING salesorders_id"
    )["salesorders_id"])
    try:
        aid = eng.submit_for_record(mod_id, rid, 1)["approval_id"]
        detail = eng.get_detail(aid)
        for s in detail["steps"]:
            assert s["approver_names"] == ["王钊"], s
    finally:
        db.execute("DELETE FROM approval_logs WHERE data_id=%s", (rid,))
        db.execute("DELETE FROM salesorders WHERE salesorders_id=%s", (rid,))


def test_approval_fields_gated_on_activation(db, mod_id):
    """审批专用字段(状态/审批人/审批时间)仅在模块激活审批时显示。"""
    from hermes.crm.models.module_fields_db import ModuleFieldsDb
    APP = {"approval_status", "approval_user_id", "approval_time"}
    mf = ModuleFieldsDb(db)
    eng = ApprovalEngine(db)

    def shown():
        return APP & {f["field_name"] for f in mf.get_visible_fields(mod_id)}

    # 未配置(_clean_approvals 已删 config)→ 不显示
    assert shown() == set()
    # 激活 → 显示(前提:这些字段已 seed 在该模块)
    eng.save_config(mod_id, [{"step_name": "经理审批", "approver_ids": [3132]}])
    # salesorders 已 seed 这 3 个字段,激活后应全部出现
    assert shown() == APP
