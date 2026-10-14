"""Preserve fractional product prices and all existing integer values."""
from alembic import op
import sqlalchemy as sa

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def _alter_price_type(sql_type):
    """Rebuild the known dashboard view inside the same DDL transaction."""
    conn = op.get_bind()
    view = conn.execute(sa.text("""SELECT c.oid, n.nspname, c.relname,
        pg_get_viewdef(c.oid) AS definition, c.relowner::regrole::text AS owner,
        c.reloptions, obj_description(c.oid, 'pg_class') AS comment
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.oid=to_regclass('v_dashboard_products') AND c.relkind='v'""")).mappings().first()
    quote = conn.dialect.identifier_preparer.quote_identifier
    if view:
        name = quote(view['nspname']) + '.' + quote(view['relname'])
        grants = conn.execute(sa.text("""SELECT a.grantee,
            CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END AS role,
            a.privilege_type, a.is_grantable
            FROM pg_class c, LATERAL aclexplode(c.relacl) a
            WHERE c.oid=:oid AND a.grantee <> c.relowner"""), {'oid': view['oid']}).mappings().all()
        # No CASCADE: unknown downstream dependencies abort and roll back.
        op.execute(f'DROP VIEW {name}')
    op.execute(f'ALTER TABLE products ALTER COLUMN price TYPE {sql_type} USING price::{sql_type}')
    if view:
        options = ', '.join(view['reloptions'] or [])
        with_options = f' WITH ({options})' if options else ''
        op.execute(f'CREATE VIEW {name}{with_options} AS {view["definition"]}')
        if view['comment'] is not None:
            comment = str(sa.literal(view['comment']).compile(dialect=conn.dialect, compile_kwargs={'literal_binds': True}))
            op.execute(f'COMMENT ON VIEW {name} IS {comment}')
        for grant in grants:
            role = 'PUBLIC' if grant['grantee'] == 0 else quote(grant['role'])
            grant_option = ' WITH GRANT OPTION' if grant['is_grantable'] else ''
            op.execute(f'GRANT {grant["privilege_type"]} ON {name} TO {role}{grant_option}')
        op.execute(f'ALTER VIEW {name} OWNER TO {quote(view["owner"])}')


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Unbounded NUMERIC preserves the entire BIGINT range as well as decimals.
    _alter_price_type('NUMERIC')
    op.execute("""UPDATE module_fields SET field_type='currency'
        WHERE field_name='price' AND module_id IN (SELECT id FROM modules WHERE name='products')
        AND field_type IN ('number', 'int', 'integer', 'currency')""")


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    bad = op.get_bind().execute(sa.text("""SELECT count(*) FROM products WHERE price IS NOT NULL
        AND (price <> trunc(price) OR price < -9223372036854775808 OR price > 9223372036854775807)""")).scalar()
    if bad:
        raise ValueError("Cannot downgrade product prices to BIGINT: fractional or out-of-range values would be lost")
    _alter_price_type('BIGINT')
    op.execute("""UPDATE module_fields SET field_type='number'
        WHERE field_name='price' AND field_type='currency'
        AND module_id IN (SELECT id FROM modules WHERE name='products')""")
