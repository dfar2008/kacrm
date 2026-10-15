"""Keep fractional opportunity amounts and product reference prices exact."""
from alembic import op
import sqlalchemy as sa

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None

MONEY_COLUMNS = (
    ("potentials", "amount", "v_dashboard_opportunities"),
    ("products", "reference_price", None),
)


def _alter_money_type(table, column, view_name, sql_type):
    conn = op.get_bind()
    quote = conn.dialect.identifier_preparer.quote_identifier
    view = None
    if view_name:
        view = conn.execute(sa.text("""SELECT c.oid, n.nspname, c.relname,
            pg_get_viewdef(c.oid) AS definition, pg_get_userbyid(c.relowner) AS owner,
            c.reloptions, obj_description(c.oid, 'pg_class') AS comment
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid=to_regclass(:name) AND c.relkind='v'"""), {'name': view_name}).mappings().first()
    if view:
        name = quote(view['nspname']) + '.' + quote(view['relname'])
        grants = conn.execute(sa.text("""SELECT a.grantee,
            CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS role,
            a.privilege_type, a.is_grantable
            FROM pg_class c, LATERAL aclexplode(c.relacl) a
            WHERE c.oid=:oid AND a.grantee <> c.relowner"""), {'oid': view['oid']}).mappings().all()
        # Unknown downstream dependencies abort the transaction; never CASCADE.
        op.execute(f'DROP VIEW {name}')
    op.execute(f'ALTER TABLE {quote(table)} ALTER COLUMN {quote(column)} TYPE {sql_type} USING {quote(column)}::{sql_type}')
    if view:
        options = ', '.join(view['reloptions'] or [])
        with_options = f' WITH ({options})' if options else ''
        op.execute(f'CREATE VIEW {name}{with_options} AS {view["definition"]}')
        if view['comment'] is not None:
            comment = str(sa.literal(view['comment']).compile(dialect=conn.dialect, compile_kwargs={'literal_binds': True}))
            op.execute(f'COMMENT ON VIEW {name} IS {comment}')
        for grant in grants:
            role = 'PUBLIC' if grant['grantee'] == 0 else quote(grant['role'])
            option = ' WITH GRANT OPTION' if grant['is_grantable'] else ''
            op.execute(f'GRANT {grant["privilege_type"]} ON {name} TO {role}{option}')
        op.execute(f'ALTER VIEW {name} OWNER TO {quote(view["owner"])}')


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, column, view in MONEY_COLUMNS:
        _alter_money_type(table, column, view, 'NUMERIC')
        op.get_bind().execute(sa.text("""UPDATE module_fields SET field_type='currency'
            WHERE module_id IN (SELECT id FROM modules WHERE name=:module)
            AND field_name=:field AND field_type IN ('number','int','integer','currency')"""),
            {'module': table, 'field': column})


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Check every column before dropping a view or changing any data type.
    for table, column, _ in MONEY_COLUMNS:
        bad = op.get_bind().execute(sa.text(f"""SELECT count(*) FROM {table}
            WHERE {column} IS NOT NULL AND ({column} <> trunc({column})
            OR {column} < -9223372036854775808 OR {column} > 9223372036854775807)""")).scalar()
        if bad:
            raise ValueError(f"Cannot downgrade {table}.{column}: fractional or out-of-range amounts would be lost")
    for table, column, view in reversed(MONEY_COLUMNS):
        _alter_money_type(table, column, view, 'BIGINT')
        op.get_bind().execute(sa.text("""UPDATE module_fields SET field_type='number'
            WHERE module_id IN (SELECT id FROM modules WHERE name=:module)
            AND field_name=:field AND field_type='currency'"""), {'module': table, 'field': column})
