"""Recovery must never bypass record permissions or the deletion actor/window."""
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException
from hermes_cloud.app.crm.payment_restore import restore_payment


def setup_db():
    db = MagicMock()
    db.transaction.side_effect = nullcontext
    db.fetchone.side_effect = [
        {'payments_id': 7, 'deleted': True, 'owner_by': 1, 'created_by': 1, 'receivables_id': 3},
        {'updated_by': 1, 'recent': True}, {'receivables_id': 3},
    ]
    return db


@pytest.mark.parametrize('allowed', [False, True])
def test_checks_row_access_before_mutation(allowed):
    db = setup_db()
    with patch('hermes_cloud.app.crm.payment_restore.Permission') as permission, patch('hermes_cloud.app.crm.payment_restore.emit'):
        permission.return_value.check_record_permission.return_value = allowed
        if not allowed:
            with pytest.raises(HTTPException) as exc:
                restore_payment(db, {'id': 5}, 7, {'id': 1})
            assert exc.value.status_code == 403
            db.execute.assert_not_called()
        else:
            assert restore_payment(db, {'id': 5}, 7, {'id': 1}) == {'success': True, 'id': 7}
            assert any('deleted = FALSE' in call.args[0] for call in db.execute.call_args_list)


@pytest.mark.parametrize('change', [{'updated_by': 2, 'recent': True}, {'updated_by': 1, 'recent': False}, None])
def test_rejects_other_actor_expired_or_missing_deletion(change):
    db = setup_db()
    db.fetchone.side_effect = [{'deleted': True}, change]
    with patch('hermes_cloud.app.crm.payment_restore.Permission') as permission:
        permission.return_value.check_record_permission.return_value = True
        with pytest.raises(HTTPException) as exc:
            restore_payment(db, {'id': 5}, 7, {'id': 1})
        assert exc.value.status_code == 409
        db.execute.assert_not_called()


def test_rejects_deleted_parent():
    db = setup_db()
    db.fetchone.side_effect = [{'deleted': True, 'receivables_id': 3}, {'updated_by': 1, 'recent': True}, None]
    with patch('hermes_cloud.app.crm.payment_restore.Permission') as permission:
        permission.return_value.check_record_permission.return_value = True
        with pytest.raises(HTTPException) as exc:
            restore_payment(db, {'id': 5}, 7, {'id': 1})
        assert exc.value.status_code == 409
        db.execute.assert_not_called()


def test_repeated_restore_cannot_double_count():
    db = setup_db()
    db.fetchone.side_effect = [{'deleted': False}, {'updated_by': 1, 'recent': True}]
    with patch('hermes_cloud.app.crm.payment_restore.Permission') as permission:
        permission.return_value.check_record_permission.return_value = True
        with pytest.raises(HTTPException) as exc:
            restore_payment(db, {'id': 5}, 7, {'id': 1})
        assert exc.value.status_code == 409
        db.execute.assert_not_called()


def test_restore_route_requires_payments_delete_permission():
    from starlette.requests import Request
    from hermes_cloud.app.crm.routes import build_router
    from hermes_cloud.app.crm import routes
    route = next(r for r in build_router().routes if r.path == '/crm/records/{module_name}/{record_id}/restore')
    dep = route.dependant.dependencies[0].call
    request = Request({'type': 'http', 'path_params': {'module_name': 'payments'}})
    with patch.object(routes, '_require_user', return_value={'id': 2}), patch.object(routes, 'Permission') as permission:
        permission.return_value.check_permission_by_module_name.return_value = False
        with pytest.raises(HTTPException) as exc:
            dep(request, MagicMock())
        assert exc.value.status_code == 403
        permission.return_value.check_permission_by_module_name.assert_called_once_with(2, 'payments', 'delete')
