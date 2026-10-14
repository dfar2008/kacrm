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


def test_decimal_price_upgrade_preserves_integers_and_refuses_lossy_downgrade():
    path = Path(__file__).resolve().parents[2] / 'alembic/versions/0116_product_decimal_price.py'
    spec = importlib.util.spec_from_file_location('kacrm_decimal_price_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(URL)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(sa.text('CREATE SCHEMA kacrm_decimal_price_test'))
            conn.execute(sa.text('SET LOCAL search_path TO kacrm_decimal_price_test'))
            conn.execute(sa.text('CREATE TABLE products(price BIGINT)'))
            conn.execute(sa.text('CREATE TABLE modules(id INT, name TEXT)'))
            conn.execute(sa.text('CREATE TABLE module_fields(module_id INT, field_name TEXT, field_type TEXT)'))
            conn.execute(sa.text("INSERT INTO modules VALUES(1,'products')"))
            conn.execute(sa.text("INSERT INTO module_fields VALUES(1,'price','number')"))
            conn.execute(sa.text('INSERT INTO products VALUES(9223372036854775807), (12), (NULL)'))
            conn.execute(sa.text('CREATE VIEW v_dashboard_products WITH (security_barrier=true) AS SELECT price FROM products'))
            conn.execute(sa.text("COMMENT ON VIEW v_dashboard_products IS 'Product dashboard'"))
            conn.execute(sa.text('GRANT SELECT ON v_dashboard_products TO PUBLIC'))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                assert conn.execute(sa.text('SELECT max(price) FROM v_dashboard_products')).scalar() == Decimal('9223372036854775807')
                assert conn.execute(sa.text("SELECT obj_description('v_dashboard_products'::regclass, 'pg_class')")).scalar() == 'Product dashboard'
                assert conn.execute(sa.text("SELECT reloptions FROM pg_class WHERE oid='v_dashboard_products'::regclass")).scalar() == ['security_barrier=true']
                assert conn.execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_class c, LATERAL aclexplode(c.relacl) a WHERE c.oid='v_dashboard_products'::regclass AND a.grantee=0 AND a.privilege_type='SELECT')")).scalar() is True
                assert conn.execute(sa.text('SELECT max(price) FROM products')).scalar() == Decimal('9223372036854775807')
                assert conn.execute(sa.text('SELECT field_type FROM module_fields')).scalar() == 'currency'
                conn.execute(sa.text('INSERT INTO products VALUES(12.50)'))
                assert conn.execute(sa.text('SELECT price FROM products WHERE price=12.50')).scalar() == Decimal('12.50')
                with pytest.raises(ValueError, match='would be lost'):
                    migration.downgrade()
                conn.execute(sa.text('DELETE FROM products WHERE price <> trunc(price)'))
                migration.downgrade()
                assert conn.execute(sa.text('SELECT max(price) FROM products')).scalar() == 9223372036854775807
                assert conn.execute(sa.text('SELECT max(price) FROM v_dashboard_products')).scalar() == 9223372036854775807
        finally:
            tx.rollback()
    engine.dispose()
