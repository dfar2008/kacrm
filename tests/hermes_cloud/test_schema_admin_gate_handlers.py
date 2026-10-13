"""管理员直建 / 非管理员入队 —— 6 个 schema handler 的身份闸。"""
from unittest.mock import MagicMock, patch
from hermes.tools.crm import dispatch as D


def test_create_module_admin_direct():
    db = MagicMock()
    with patch.object(D, "_actor_is_admin", return_value=True), \
         patch("hermes_cloud.app.crm.module_provisioning.provision_module",
               return_value={"module_id": 11, "name": "expenses"}) as pm, \
         patch.object(D, "_enqueue_schema_proposal") as enq:
        out = D._dispatch_create_module(
            {"name": "expenses", "title": "报销", "reason": "x"},
            user_id=1, db=db,
        )
    assert out["success"] is True and out["module_id"] == 11
    pm.assert_called_once()
    enq.assert_not_called()


def test_create_module_nonadmin_enqueues():
    db = MagicMock()
    with patch.object(D, "_actor_is_admin", return_value=False), \
         patch.object(D, "_validate_module_name_pure", return_value=None), \
         patch("hermes_cloud.app.crm.module_provisioning.provision_module") as pm, \
         patch.object(D, "_enqueue_schema_proposal",
                      return_value={"success": True, "status": "pending", "proposal_id": 5}) as enq:
        out = D._dispatch_create_module(
            {"name": "expenses", "title": "报销", "reason": "用户要建报销"},
            user_id=9, db=db,
        )
    assert out["status"] == "pending"
    pm.assert_not_called()
    assert enq.call_args.kwargs["kind"] == "module_add"
    assert enq.call_args.kwargs["dedup_key"] == "expenses"
    assert enq.call_args.kwargs["module_id"] is None


def test_create_module_nonadmin_invalid_name_rejected_before_enqueue():
    db = MagicMock()
    with patch.object(D, "_actor_is_admin", return_value=False), \
         patch.object(D, "_validate_module_name_pure",
                      side_effect=ValueError("invalid module name")), \
         patch.object(D, "_enqueue_schema_proposal") as enq:
        out = D._dispatch_create_module(
            {"name": "Bad Name", "title": "x", "reason": "y"}, user_id=9, db=db,
        )
    assert out["success"] is False
    assert "invalid" in out["error"].lower()
    enq.assert_not_called()


def test_create_field_nonadmin_enqueues_full_spec():
    db = MagicMock()
    mod = {"id": 3, "name": "accounts", "table_name": "accounts"}
    with patch.object(D, "_actor_is_admin", return_value=False), \
         patch.object(D, "ModulesDb") as MD, \
         patch.object(D, "ModuleFieldsDb") as MF, \
         patch.object(D, "_enqueue_schema_proposal",
                      return_value={"success": True, "status": "pending"}) as enq:
        MD.return_value.get_module_by_name.return_value = mod
        MF.return_value.get_field_by_name.return_value = None
        MF.return_value.get_module_fields.return_value = []
        out = D._dispatch_create_field(
            {"module_name": "accounts", "field_name": "region",
             "field_label": "区域", "field_type": "relation",
             "target_module": "areas", "reason": "用户要"},
            user_id=9, db=db,
        )
    # relation 字段也能入队(类型不缩水)
    assert out["status"] == "pending"
    pl = enq.call_args.kwargs["payload"]
    assert pl["field_type"] == "relation"
    assert pl["field_config"]["relation"]["target_module"] == "areas"
    assert enq.call_args.kwargs["kind"] == "field_add"


def test_set_writeback_admin_direct():
    db = MagicMock()
    with patch.object(D, "_actor_is_admin", return_value=True), \
         patch("hermes.crm.models.modules_db.ModulesDb") as MD, \
         patch.object(D, "_resolve_field", side_effect=[{"id": 71}, {"id": 31}]), \
         patch("hermes.crm.field_writeback.FieldWriteback") as FW, \
         patch.object(D, "_enqueue_schema_proposal") as enq:
        MD.return_value.get_module_by_name.side_effect = [{"id": 7}, {"id": 3}]
        FW.return_value.create_rule.return_value = {"success": True, "id": 99}
        out = D._dispatch_set_field_writeback_rule(
            {"source_module_name": "order_items", "source_field_name": "amount",
             "target_module_name": "salesorders", "target_field_name": "total_amount",
             "calc_method": "sum"},
            user_id=1, db=db,
        )
    assert out["success"] is True and out["id"] == 99
    FW.return_value.create_rule.assert_called_once()
    enq.assert_not_called()


def test_set_writeback_nonadmin_enqueues():
    db = MagicMock()
    with patch.object(D, "_actor_is_admin", return_value=False), \
         patch("hermes.crm.models.modules_db.ModulesDb") as MD, \
         patch.object(D, "_resolve_field", side_effect=[{"id": 71}, {"id": 31}]), \
         patch("hermes.crm.field_writeback.FieldWriteback") as FW, \
         patch.object(D, "_enqueue_schema_proposal",
                      return_value={"success": True, "status": "pending", "proposal_id": 8}) as enq:
        MD.return_value.get_module_by_name.side_effect = [{"id": 7}, {"id": 3}]
        out = D._dispatch_set_field_writeback_rule(
            {"source_module_name": "order_items", "source_field_name": "amount",
             "target_module_name": "salesorders", "target_field_name": "total_amount",
             "calc_method": "sum", "reason": "明细汇总到总额"},
            user_id=9, db=db,
        )
    # 非管理员 → 入队提案,不直接落库
    assert out["status"] == "pending"
    FW.return_value.create_rule.assert_not_called()
    assert enq.call_args.kwargs["kind"] == "writeback_add"
    assert enq.call_args.kwargs["module_id"] == 7          # 源模块 id
    assert enq.call_args.kwargs["dedup_key"] == "71->31:sum"
    pl = enq.call_args.kwargs["payload"]
    assert pl["source_field_id"] == 71 and pl["target_field_id"] == 31
    assert pl["calc_method"] == "sum"


def test_remove_writeback_requires_admin():
    db = MagicMock()
    with patch.object(D, "_require_admin",
                      side_effect=D.ToolPermissionError("admin only")) as ra:
        try:
            D._dispatch_remove_field_writeback_rule({"rule_id": 5}, user_id=9, db=db)
            assert False, "expected ToolPermissionError"
        except D.ToolPermissionError:
            pass
    ra.assert_called_once()


def test_create_workflow_nonadmin_enqueues():
    db = MagicMock()
    with patch.object(D, "_actor_is_admin", return_value=False), \
         patch.object(D, "ModulesDb") as MD, \
         patch.object(D, "_enqueue_schema_proposal",
                      return_value={"success": True, "status": "pending"}) as enq:
        MD.return_value.get_module_by_name.return_value = {"id": 4, "name": "leads"}
        out = D._dispatch_create_workflow(
            {"name": "dunning", "trigger_type": "event", "module": "leads",
             "trigger_on": "create", "actions": [{"x": 1}], "reason": "用户要"},
            user_id=9, db=db,
        )
    assert out["status"] == "pending"
    assert enq.call_args.kwargs["kind"] == "workflow_add"
    assert enq.call_args.kwargs["module_id"] == 4
