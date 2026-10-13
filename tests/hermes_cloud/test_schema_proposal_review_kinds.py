"""review 端点扩 kind + 归属落原申请人。"""
import inspect
import hermes_cloud.app.crm.routes as routes_mod
from hermes_cloud.app.crm.routes import _proposer_actor


def test_review_handles_all_new_kinds():
    src = inspect.getsource(routes_mod)
    # 四个新 kind 分支都在,且各自接到正确的 provisioner。
    assert 'kind == "module_add"' in src
    assert 'kind == "subdetail_add"' in src
    assert 'kind == "module_rename"' in src
    assert 'kind == "workflow_add"' in src
    assert 'kind == "writeback_add"' in src
    assert "provision_module(" in src
    assert "provision_subdetail(" in src
    assert "rename_module(" in src
    assert "provision_workflow(" in src
    assert "FieldWriteback(" in src


def test_proposer_actor_prefers_proposer():
    actor = _proposer_actor(
        {"proposer_id": 9, "proposer_label": "wang"},
        reviewer={"id": 1, "username": "admin"},
    )
    assert actor["id"] == 9            # 原申请人,不是审批管理员
    assert actor["username"] == "wang"


def test_proposer_actor_falls_back_to_reviewer_when_no_proposer():
    actor = _proposer_actor(
        {"proposer_id": None, "proposer_label": None},
        reviewer={"id": 1, "username": "admin"},
    )
    assert actor["id"] == 1
    assert actor["username"] == "admin"
