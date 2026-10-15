"""Real PostgreSQL coverage for transactional receipt/order rollups."""
import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

URL = os.environ.get('KACRM_TEST_DATABASE_URL')
pytestmark = [pytest.mark.requires_real_pg, pytest.mark.skipif(not URL, reason='KACRM_TEST_DATABASE_URL is required')]


@pytest.fixture
def cashflow():
    path = Path(__file__).resolve().parents[2] / 'alembic/versions/0118_cashflow_rollups.py'
    spec = importlib.util.spec_from_file_location('kacrm_cashflow_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(URL)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            schema = 'kacrm_cashflow_' + uuid4().hex
            conn.execute(sa.text(f'CREATE SCHEMA {schema}'))
            conn.execute(sa.text(f'SET LOCAL search_path TO {schema}'))
            conn.execute(sa.text('CREATE TABLE salesorders(salesorders_id BIGINT PRIMARY KEY, final_amount NUMERIC(18,2), receivables_amount NUMERIC(18,2), payments_amount NUMERIC(18,2), latest_payment_date TIMESTAMPTZ, payment_status TEXT, deleted BOOLEAN DEFAULT false)'))
            conn.execute(sa.text('CREATE TABLE receivables(receivables_id BIGINT PRIMARY KEY, salesorders_id BIGINT, dueamount NUMERIC(18,2), actualamount NUMERIC(18,2), balance NUMERIC(18,2), actualdate DATE, duedate DATE, receivablesstatus TEXT, deleted BOOLEAN DEFAULT false)'))
            conn.execute(sa.text('CREATE TABLE payments(payments_id BIGINT PRIMARY KEY, receivables_id BIGINT, payamount NUMERIC(18,2), paydate DATE, deleted BOOLEAN DEFAULT false)'))
            conn.execute(sa.text('CREATE TABLE modules(id BIGINT, name TEXT)'))
            conn.execute(sa.text('CREATE TABLE module_fields(module_id BIGINT, field_name TEXT, field_type TEXT, options TEXT)'))
            conn.execute(sa.text("INSERT INTO modules VALUES(1,'salesorders')"))
            conn.execute(sa.text("INSERT INTO module_fields VALUES(1,'payment_status','text',NULL)"))
            conn.execute(sa.text("INSERT INTO salesorders VALUES(99,100,100,40,NULL,'partial',false)"))
            conn.execute(sa.text("INSERT INTO receivables VALUES(99,99,100,40,60,'2026-01-01','2099-01-01','partial',false)"))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            yield conn, migration
        finally:
            if tx.is_active:
                tx.rollback()
            else:
                conn.rollback()
                conn.execute(sa.text(f'DROP SCHEMA {schema} CASCADE'))
                conn.commit()
    engine.dispose()


def row(conn, table, key):
    return conn.execute(sa.text(f'SELECT * FROM {table} WHERE {table}_id=:id'), {'id': key}).mappings().one()


def seed(conn):
    conn.execute(sa.text('INSERT INTO salesorders(salesorders_id,final_amount) VALUES(1,25.50),(2,25.50)'))
    conn.execute(sa.text("INSERT INTO receivables(receivables_id,salesorders_id,dueamount,duedate) VALUES(1,1,25.50,'2099-01-01'),(2,2,25.50,'2099-01-01')"))


def check(conn, rid, paid, balance, status, order_status=None):
    r = row(conn, 'receivables', rid)
    assert (r['actualamount'], r['balance'], r['receivablesstatus']) == (Decimal(paid), Decimal(balance), status)
    order = row(conn, 'salesorders', r['salesorders_id'])
    assert order['payments_amount'] == Decimal(paid)
    assert order['payment_status'] == (order_status or status)


def test_partial_full_edit_delete_restore_move_and_order_amount(cashflow):
    conn, migration = cashflow
    seed(conn)
    check(conn, 1, '0', '25.50', 'pending')
    assert row(conn, 'salesorders', 1)['receivables_amount'] == Decimal('25.50')
    conn.execute(sa.text("INSERT INTO payments VALUES(1,1,10.25,'2026-10-15',false)"))
    check(conn, 1, '10.25', '15.25', 'partial')
    conn.execute(sa.text("INSERT INTO payments VALUES(2,1,15.25,'2026-10-16',false)"))
    check(conn, 1, '25.50', '0', 'received')
    assert str(row(conn, 'receivables', 1)['actualdate']) == '2026-10-16'
    conn.execute(sa.text('UPDATE payments SET payamount=10.00 WHERE payments_id=2'))
    check(conn, 1, '20.25', '5.25', 'partial')
    conn.execute(sa.text('UPDATE payments SET receivables_id=2 WHERE payments_id=2'))
    check(conn, 1, '10.25', '15.25', 'partial')
    check(conn, 2, '10', '15.50', 'partial')
    conn.execute(sa.text('UPDATE payments SET deleted=true WHERE payments_id=1'))
    check(conn, 1, '0', '25.50', 'pending')
    assert row(conn, 'receivables', 1)['actualdate'] is None
    conn.execute(sa.text('UPDATE payments SET deleted=false WHERE payments_id=1'))
    check(conn, 1, '10.25', '15.25', 'partial')
    conn.execute(sa.text('DELETE FROM payments WHERE payments_id=1'))
    check(conn, 1, '0', '25.50', 'pending')
    conn.execute(sa.text('UPDATE salesorders SET final_amount=5 WHERE salesorders_id=2'))
    assert row(conn, 'salesorders', 2)['payment_status'] == 'received'
    conn.execute(sa.text('UPDATE receivables SET salesorders_id=1 WHERE receivables_id=2'))
    assert row(conn, 'salesorders', 2)['payments_amount'] == 0
    assert row(conn, 'salesorders', 1)['payments_amount'] == 10
    conn.execute(sa.text('UPDATE receivables SET deleted=true WHERE receivables_id=2'))
    assert row(conn, 'salesorders', 1)['payments_amount'] == 0
    conn.execute(sa.text('UPDATE receivables SET deleted=false WHERE receivables_id=2'))
    assert row(conn, 'salesorders', 1)['payments_amount'] == 10


def test_legacy_opening_amounts_are_preserved_and_migration_has_no_backfill(cashflow):
    conn, migration = cashflow
    check(conn, 99, '40', '60', 'partial')
    conn.execute(sa.text("INSERT INTO payments VALUES(1,99,10.25,'2026-10-15',false)"))
    check(conn, 99, '50.25', '49.75', 'partial')
    conn.execute(sa.text('UPDATE payments SET deleted=true WHERE payments_id=1'))
    check(conn, 99, '40', '60', 'partial')
    assert row(conn, 'receivables', 99)['actualdate'] is not None
    with Operations.context(MigrationContext.configure(conn)):
        migration.downgrade()
    assert row(conn, 'receivables', 99)['actualamount'] == Decimal('40')
    assert conn.execute(sa.text("SELECT field_type FROM module_fields WHERE field_name='payment_status'")).scalar() == 'text'


def test_overdue_zero_null_and_preexisting_unrolled_payment(cashflow):
    conn, _ = cashflow
    seed(conn)
    conn.execute(sa.text("UPDATE receivables SET duedate=CURRENT_DATE-1 WHERE receivables_id=1"))
    check(conn, 1, '0', '25.50', 'overdue', 'pending')
    conn.execute(sa.text("INSERT INTO payments VALUES(1,1,25.50,'2026-10-15',false)"))
    check(conn, 1, '25.50', '0', 'received')
    conn.execute(sa.text('UPDATE receivables SET dueamount=30.75 WHERE receivables_id=1'))
    check(conn, 1, '25.50', '5.25', 'partial', 'received')
    # Amounts entered before the migration may be NULL, despite having payments.
    conn.execute(sa.text('ALTER TABLE receivables DISABLE TRIGGER kacrm_receivable_values'))
    conn.execute(sa.text('UPDATE receivables SET actualamount=NULL WHERE receivables_id=1'))
    conn.execute(sa.text('ALTER TABLE receivables ENABLE TRIGGER kacrm_receivable_values'))
    conn.execute(sa.text('UPDATE payments SET payamount=payamount WHERE payments_id=1'))
    check(conn, 1, '25.50', '5.25', 'partial', 'received')


@pytest.mark.parametrize('same_parent', [True, False])
def test_concurrent_payments_do_not_lose_receivable_or_order_totals(cashflow, same_parent):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    conn, _ = cashflow
    seed(conn)
    if not same_parent:
        conn.execute(sa.text('UPDATE receivables SET salesorders_id=1 WHERE receivables_id=2'))
    schema = conn.execute(sa.text('SELECT current_schema()')).scalar()
    conn.commit()
    barrier = Barrier(2)
    engine = sa.create_engine(URL)
    def pay(pid, rid, amount):
        with engine.begin() as worker:
            worker.execute(sa.text(f'SET LOCAL search_path TO {schema}'))
            worker.execute(sa.text("SET LOCAL statement_timeout='5s'"))
            barrier.wait(timeout=5)
            worker.execute(sa.text("INSERT INTO payments VALUES(:pid,:rid,:amount,'2026-10-15',false)"),
                           {'pid': pid, 'rid': rid, 'amount': amount})
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(pay, 1, 1, '10.25')
            b = pool.submit(pay, 2, 1 if same_parent else 2, '15.25')
            a.result(timeout=10)
            b.result(timeout=10)
        conn.execute(sa.text(f'SET LOCAL search_path TO {schema}'))
        order = row(conn, 'salesorders', 1)
        assert order['payments_amount'] == Decimal('25.50')
        assert order['payment_status'] == 'received'
        assert conn.execute(sa.text('SELECT SUM(actualamount) FROM receivables WHERE salesorders_id=1')).scalar() == Decimal('25.50')
    finally:
        engine.dispose()


def test_order_payment_status_has_contextual_localized_labels(cashflow):
    from hermes.crm.i18n.options import parse_picklist_options, localize_picklist_options
    conn, _ = cashflow
    raw = conn.execute(sa.text("SELECT options FROM module_fields WHERE field_name='payment_status'")).scalar()
    options = localize_picklist_options(parse_picklist_options(raw), locale='zh-CN')
    assert {o['value']: o['text'] for o in options} == {'pending': '待回款', 'partial': '部分回款', 'received': '已回款'}


def test_restored_order_reflects_receipts_changed_while_it_was_deleted(cashflow):
    conn, _ = cashflow
    seed(conn)
    conn.execute(sa.text("INSERT INTO payments VALUES(1,1,10.25,'2026-10-15',false)"))
    conn.execute(sa.text('UPDATE salesorders SET deleted=true WHERE salesorders_id=1'))
    conn.execute(sa.text('UPDATE payments SET payamount=25.50 WHERE payments_id=1'))
    conn.execute(sa.text('UPDATE salesorders SET deleted=false WHERE salesorders_id=1'))
    assert row(conn, 'salesorders', 1)['payments_amount'] == Decimal('25.50')
    assert row(conn, 'salesorders', 1)['payment_status'] == 'received'
