"""Undo a recent payment deletion without recreating or duplicating the payment."""
from fastapi import HTTPException
from hermes.crm.permission import Permission
from hermes.crm.models.changelog_db import ChangelogDb
from hermes_cloud.app.interactions import emit


def restore_payment(db, module: dict, record_id: int, user: dict) -> dict:
    # Route also requires module delete permission. Locking serializes retries;
    # the second request cannot apply the payment rollup a second time.
    with db.transaction():
        row = db.fetchone('SELECT * FROM payments WHERE payments_id = %s FOR UPDATE', (record_id,))
        if not row:
            raise HTTPException(404, 'Payment not found')
        if not Permission(db).check_record_permission(
            int(module['id']), int(user['id']), record_id,
            row.get('owner_by') or 0, row.get('created_by') or 0, 'delete',
        ):
            raise HTTPException(403, 'No permission to restore this payment')
        deletion = db.fetchone(
            "SELECT updated_by, updated_at >= now() - interval '120 seconds' AS recent "
            "FROM changelog WHERE module_id = %s AND data_id = %s "
            "AND field_name = 'deleted' ORDER BY updated_at DESC, tracking_id DESC LIMIT 1",
            (int(module['id']), record_id),
        )
        if not row.get('deleted') or not deletion or not deletion['recent'] or int(deletion['updated_by'] or 0) != int(user['id']):
            raise HTTPException(409, 'Only your own deletion within 120 seconds can be restored')
        if row.get('receivables_id') and not db.fetchone(
            'SELECT receivables_id FROM receivables WHERE receivables_id = %s AND NOT deleted FOR KEY SHARE',
            (row['receivables_id'],),
        ):
            raise HTTPException(409, 'Restore the linked receivable first')
        db.execute('UPDATE payments SET deleted = FALSE WHERE payments_id = %s', (record_id,))
        ChangelogDb(db).log_field_change(int(module['id']), record_id, 'deleted', 'Deleted', 'status', '1', '0', int(user['id']))
        emit(kind='action', subjects=[{'module': 'payments', 'id': record_id}],
             actors=[{'type': 'user', 'id': int(user['id'])}],
             content={'op': 'restore'}, source='manual', db=db)
    return {'success': True, 'id': record_id}
