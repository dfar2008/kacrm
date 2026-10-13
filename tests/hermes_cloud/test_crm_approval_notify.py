"""审批渠道通知推送测试(notify_approvers + home_channel 解析)。"""
from __future__ import annotations

import pytest

from hermes.crm.approval.engine import ApprovalEngine


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

_SO = "salesorders"
_WANGZ = 3132  # 王钊,username/profile = 'wangz-914b'


@pytest.fixture
def db():
    from hermes.crm.db import db_session

    with db_session() as conn:
        yield conn


@pytest.fixture
def mod_id(db) -> int:
    return int(db.fetchone("SELECT id FROM modules WHERE name=%s", (_SO,))["id"])


@pytest.fixture(autouse=True)
def _clean(db, mod_id):
    def _wipe():
        db.execute(
            "DELETE FROM approval_steps WHERE approval_id IN "
            "(SELECT id FROM approvals WHERE module_id=%s)", (mod_id,))
        db.execute("DELETE FROM approvals WHERE module_id=%s", (mod_id,))
        db.execute("DELETE FROM approval_configs WHERE module_id=%s", (mod_id,))
    _wipe()
    yield
    _wipe()


def test_get_home_channels(tmp_path):
    from hermes_cloud.app.crm.approval_notify import _get_home_channels
    from hermes_cloud.app.channel_config import set_home_channel

    pdir = tmp_path / "profiles" / "u1"
    pdir.mkdir(parents=True)
    set_home_channel(pdir, "dingtalk", "cid_y")
    set_home_channel(pdir, "feishu", "ou_xxx")
    chans = _get_home_channels(pdir)
    assert ("feishu", "ou_xxx") in chans
    assert ("dingtalk", "cid_y") in chans

    empty = tmp_path / "profiles" / "u2"
    empty.mkdir(parents=True)
    assert _get_home_channels(empty) == []


def test_profile_id_for_user(db):
    from hermes_cloud.app.crm.approval_notify import _profile_id_for_user

    assert _profile_id_for_user(db, _WANGZ) == "wangz-914b"


def test_notify_approvers_pushes(db, mod_id, tmp_path, monkeypatch):
    from hermes_cloud.app.crm.approval_notify import notify_approvers
    from hermes_cloud.app.channel_config import set_home_channel
    import hermes_cloud.app.insights.notify as notify_mod

    # 给王钊的临时 profile 配 home_channel
    pdir = tmp_path / "profiles" / "wangz-914b"
    pdir.mkdir(parents=True)
    set_home_channel(pdir, "feishu", "ou_wangz")

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, [{"step_name": "经理审批", "approver_ids": [_WANGZ]}])
    aid = eng.submit_for_record(mod_id, 999500, 1)["approval_id"]

    calls = []
    monkeypatch.setattr(notify_mod, "_post_via_cloud", lambda **kw: calls.append(kw) or True)
    out = notify_approvers(db, aid, hermes_home=tmp_path)

    assert out["pushed"] == 1 and out["skipped"] == 0
    assert calls[0]["platform"] == "feishu"
    assert calls[0]["channel_id"] == "ou_wangz"
    assert "待审批" in calls[0]["text"]
    # 审批单号用 SP-<id>(明显区别于订单号 #,避免 agent 混淆)。
    assert f"SP-{aid}" in calls[0]["text"]
    assert "同意 SP-" in calls[0]["text"]


def test_notify_approvers_falls_through_to_working_channel(db, mod_id, tmp_path, monkeypatch):
    """钉钉(第一个)推送失败 → 自动回退到飞书,只算一次成功。"""
    from hermes_cloud.app.crm.approval_notify import notify_approvers
    from hermes_cloud.app.channel_config import set_home_channel
    import hermes_cloud.app.insights.notify as notify_mod

    pdir = tmp_path / "profiles" / "wangz-914b"
    pdir.mkdir(parents=True)
    set_home_channel(pdir, "dingtalk", "cid_fail")   # 先尝试,模拟失败
    set_home_channel(pdir, "feishu", "ou_ok")        # 回退成功

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, [{"step_name": "经理审批", "approver_ids": [_WANGZ]}])
    aid = eng.submit_for_record(mod_id, 999503, 1)["approval_id"]

    tried = []

    def _fake_push(**kw):
        tried.append(kw["platform"])
        return kw["platform"] != "dingtalk"  # 钉钉失败,其余成功

    monkeypatch.setattr(notify_mod, "_post_via_cloud", _fake_push)
    out = notify_approvers(db, aid, hermes_home=tmp_path)

    assert out["pushed"] == 1
    assert "dingtalk" in tried and "feishu" in tried  # 钉钉先试、失败后试了飞书


def test_notify_approvers_skips_when_no_binding(db, mod_id, tmp_path, monkeypatch):
    from hermes_cloud.app.crm.approval_notify import notify_approvers
    import hermes_cloud.app.insights.notify as notify_mod

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, [{"step_name": "经理审批", "approver_ids": [_WANGZ]}])
    aid = eng.submit_for_record(mod_id, 999501, 1)["approval_id"]

    calls = []
    monkeypatch.setattr(notify_mod, "_post_via_cloud", lambda **kw: calls.append(kw) or True)
    # tmp_path 下没有任何 profile 配置 → 无 home_channel → skip
    out = notify_approvers(db, aid, hermes_home=tmp_path)

    assert out == {"pushed": 0, "skipped": 1}
    assert not calls


def test_workflow_submit_triggers_notify(db, mod_id, monkeypatch):
    from hermes_cloud.app.crm.workflows import _execute_engine_action
    import hermes_cloud.app.crm.approval_notify as an

    eng = ApprovalEngine(db)
    eng.save_config(mod_id, [{"step_name": "经理审批", "approver_ids": [_WANGZ]}])
    called = []
    monkeypatch.setattr(
        an, "notify_approvers",
        lambda db, aid: called.append(aid) or {"pushed": 0, "skipped": 0},
    )
    wf = {"module": _SO, "name": "wf", "_trigger": "add"}
    _execute_engine_action(
        db, {"kind": "submit_approval", "params": {}}, wf, {"salesorders_id": 999502}, 1
    )
    assert called  # 工作流提交后触发了渠道通知


def test_record_details_includes_customer_and_line_items(db, mod_id):
    """详情块含 记录名/金额 + 产品明细子明细,方便审批人查看。"""
    from hermes_cloud.app.crm.approval_notify import _record_details

    rid = int(db.fetchone(
        "INSERT INTO salesorders (salesorders_name, final_amount, deleted) "
        "VALUES ('明细测试单', 150000, FALSE) RETURNING salesorders_id"
    )["salesorders_id"])
    try:
        for nm, qty, tot in [("Office套件", 100, 100000), ("CRM授权", 1, 50000)]:
            db.execute(
                "INSERT INTO salesorders_products "
                "(salesorders_id, products_id, productno, products_name, quantity, total) "
                "VALUES (%s, 0, %s, %s, %s, %s)",
                (rid, nm, nm, qty, tot),
            )
        details = _record_details(db, "salesorders", "salesorders", rid, "销售订单")
        assert "明细测试单" in details
        # 基本信息走模块字段标签(最终金额),金额格式化带 ¥
        assert "¥150,000" in details
        assert "产品明细" in details
        assert "Office套件" in details and "CRM授权" in details
        assert "×100" in details
    finally:
        db.execute("DELETE FROM salesorders_products WHERE salesorders_id = %s", (rid,))
        db.execute("DELETE FROM salesorders WHERE salesorders_id = %s", (rid,))
