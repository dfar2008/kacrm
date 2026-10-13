"""非管理员 schema 直建 → 入队 的两个辅助。"""
from unittest.mock import MagicMock, patch
from hermes.tools.crm import dispatch as D


def test_actor_is_admin_true_false():
    db = MagicMock()
    db.fetchone.return_value = {"is_admin": True}
    assert D._actor_is_admin(db, 5) is True
    db.fetchone.return_value = {"is_admin": False}
    assert D._actor_is_admin(db, 5) is False
    db.fetchone.return_value = None
    assert D._actor_is_admin(db, 0) is False   # 取不到身份 → 非管理员


def test_enqueue_schema_proposal_inserts_and_returns_pending():
    db = MagicMock()
    with patch.object(D, "FieldProposalsDb") as FP:
        inst = FP.return_value
        inst.find_pending_by_kind_key.return_value = None
        inst.insert_proposal.return_value = 42
        out = D._enqueue_schema_proposal(
            db, kind="module_add", module_id=None, module_name="expenses",
            dedup_key="expenses",
            payload={"name": "expenses", "title": "报销"},
            reason="用户要建报销模块", user_id=9,
            profile_id="p1", agent_name="hermes",
        )
    assert out["success"] is True
    assert out["status"] == "pending"
    assert out["proposal_id"] == 42
    kwargs = inst.insert_proposal.call_args.kwargs
    assert kwargs["proposer_type"] == "agent"
    assert kwargs["proposer_id"] == 9
    assert kwargs["payload"]["kind"] == "module_add"
    assert kwargs["payload"]["_dedup_key"] == "expenses"
    assert kwargs["payload"]["reason"] == "用户要建报销模块"


def test_enqueue_dedup_returns_existing():
    db = MagicMock()
    with patch.object(D, "FieldProposalsDb") as FP:
        inst = FP.return_value
        inst.find_pending_by_kind_key.return_value = {"id": 7}
        out = D._enqueue_schema_proposal(
            db, kind="module_add", module_id=None, module_name="expenses",
            dedup_key="expenses", payload={"name": "expenses"},
            reason="再建一次", user_id=9, profile_id="p1", agent_name="hermes",
        )
    assert out["status"] == "pending"
    assert out["deduped"] is True
    assert out["proposal_id"] == 7
    inst.insert_proposal.assert_not_called()
