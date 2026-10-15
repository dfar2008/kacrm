import importlib.util
import os
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

URL = os.environ.get('KACRM_TEST_DATABASE_URL')
pytestmark = [pytest.mark.requires_real_pg, pytest.mark.skipif(not URL, reason='KACRM_TEST_DATABASE_URL is required')]


def test_legacy_money_columns_preserve_decimals_and_dashboard_permissions():
    path = Path(__file__).resolve().parents[2] / 'alembic/versions/0117_decimal_money_fields.py'
    spec = importlib.util.spec_from_file_location('kacrm_decimal_money_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(URL)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(sa.text('CREATE SCHEMA kacrm_decimal_money_test'))
            conn.execute(sa.text('SET LOCAL search_path TO kacrm_decimal_money_test'))
            conn.execute(sa.text('CREATE TABLE potentials(amount BIGINT)'))
            conn.execute(sa.text('CREATE TABLE products(reference_price BIGINT, price NUMERIC, stock BIGINT)'))
            conn.execute(sa.text('CREATE TABLE modules(id INT, name TEXT)'))
            conn.execute(sa.text('CREATE TABLE module_fields(module_id INT, field_name TEXT, field_type TEXT)'))
            conn.execute(sa.text("INSERT INTO modules VALUES(1,'potentials'),(2,'products')"))
            conn.execute(sa.text("INSERT INTO module_fields VALUES(1,'amount','number'),(2,'reference_price','number'),(2,'stock','number')"))
            conn.execute(sa.text('INSERT INTO potentials VALUES(9223372036854775807), (25), (NULL)'))
            conn.execute(sa.text('INSERT INTO products VALUES(9223372036854775807, 12.50, 10), (NULL, NULL, 0)'))
            conn.execute(sa.text('CREATE VIEW v_dashboard_opportunities WITH (security_barrier=true) AS SELECT amount FROM potentials'))
            conn.execute(sa.text("COMMENT ON VIEW v_dashboard_opportunities IS 'Opportunity dashboard'"))
            conn.execute(sa.text('GRANT SELECT ON v_dashboard_opportunities TO PUBLIC'))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                conn.execute(sa.text("INSERT INTO potentials VALUES('25.50')"))
                conn.execute(sa.text("INSERT INTO products VALUES('12.75', 12.50, 10)"))
                assert conn.execute(sa.text('SELECT min(amount) FROM potentials WHERE amount>25')).scalar() == Decimal('25.50')
                assert conn.execute(sa.text('SELECT min(reference_price) FROM products')).scalar() == Decimal('12.75')
                assert conn.execute(sa.text('SELECT max(amount) FROM v_dashboard_opportunities')).scalar() == Decimal('9223372036854775807')
                assert conn.execute(sa.text('SELECT max(reference_price) FROM products')).scalar() == Decimal('9223372036854775807')
                assert conn.execute(sa.text("SELECT obj_description('v_dashboard_opportunities'::regclass, 'pg_class')")).scalar() == 'Opportunity dashboard'
                assert conn.execute(sa.text("SELECT reloptions FROM pg_class WHERE oid='v_dashboard_opportunities'::regclass")).scalar() == ['security_barrier=true']
                assert conn.execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_class c, LATERAL aclexplode(c.relacl) a WHERE c.oid='v_dashboard_opportunities'::regclass AND a.grantee=0 AND a.privilege_type='SELECT')")).scalar()
                assert conn.execute(sa.text("SELECT count(*) FROM module_fields WHERE field_type='currency'")).scalar() == 2
                assert conn.execute(sa.text("SELECT field_type FROM module_fields WHERE field_name='stock'")).scalar() == 'number'
                with pytest.raises(ValueError, match='would be lost'):
                    migration.downgrade()
                conn.execute(sa.text('DELETE FROM potentials WHERE amount <> trunc(amount)'))
                conn.execute(sa.text('DELETE FROM products WHERE reference_price <> trunc(reference_price)'))
                migration.downgrade()
                assert conn.execute(sa.text('SELECT max(amount) FROM v_dashboard_opportunities')).scalar() == 9223372036854775807
                assert conn.execute(sa.text('SELECT price FROM products WHERE stock=10')).scalar() == Decimal('12.50')
        finally:
            tx.rollback()
    engine.dispose()
