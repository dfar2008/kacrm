"""Maintain receivable balances and order collection totals transactionally.

Payment changes apply their net delta so historical manually entered opening
receipts remain intact. No historical records are backfilled by this migration.
Triggers cover browser/agent writes, soft deletion, restore and reassignment.
"""
import json
from alembic import op

revision = '0118'
down_revision = '0117'
branch_labels = None
depends_on = None

PAYMENT_OPTIONS = json.dumps([
    {'value': 'pending', 'text': '待回款', 'label_i18n': {'zh-CN': '待回款', 'en-US': 'Pending'}},
    {'value': 'partial', 'text': '部分回款', 'label_i18n': {'zh-CN': '部分回款', 'en-US': 'Partially received'}},
    {'value': 'received', 'text': '已回款', 'label_i18n': {'zh-CN': '已回款', 'en-US': 'Received'}},
], ensure_ascii=False)

SQL = r'''
CREATE FUNCTION kacrm_cashflow_order(p_id BIGINT) RETURNS void AS $$
DECLARE due NUMERIC; paid NUMERIC; last_date TIMESTAMPTZ; amount NUMERIC;
BEGIN
    IF p_id IS NULL THEN RETURN; END IF;
    SELECT final_amount INTO amount FROM salesorders
      WHERE salesorders_id=p_id AND NOT deleted FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT COALESCE(SUM(dueamount),0), COALESCE(SUM(actualamount),0), MAX(actualdate)::timestamptz
      INTO due, paid, last_date FROM receivables
      WHERE salesorders_id=p_id AND NOT deleted;
    UPDATE salesorders SET receivables_amount=due, payments_amount=paid,
      latest_payment_date=last_date,
      payment_status=CASE WHEN paid<=0 THEN 'pending'
        WHEN paid>=COALESCE(amount,due) THEN 'received' ELSE 'partial' END
      WHERE salesorders_id=p_id;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION kacrm_cashflow_receivable_values() RETURNS trigger AS $$
BEGIN
    IF NEW.actualamount IS NULL THEN
        SELECT COALESCE(SUM(payamount),0) INTO NEW.actualamount FROM payments
          WHERE receivables_id=NEW.receivables_id AND NOT deleted;
    END IF;
    NEW.balance := GREATEST(COALESCE(NEW.dueamount,0)-NEW.actualamount,0);
    NEW.receivablesstatus := CASE
      WHEN NEW.dueamount IS NOT NULL AND NEW.actualamount>=NEW.dueamount THEN 'received'
      WHEN NEW.actualamount>0 THEN 'partial'
      WHEN NEW.duedate<CURRENT_DATE THEN 'overdue' ELSE 'pending' END;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION kacrm_cashflow_receivable_changed() RETURNS trigger AS $$
DECLARE old_id BIGINT; new_id BIGINT; p_id BIGINT;
BEGIN
    IF TG_OP<>'INSERT' THEN old_id := OLD.salesorders_id; END IF;
    IF TG_OP<>'DELETE' THEN new_id := NEW.salesorders_id; END IF;
    FOR p_id IN SELECT DISTINCT x FROM unnest(ARRAY[old_id,new_id]) x
      WHERE x IS NOT NULL ORDER BY x LOOP
        PERFORM kacrm_cashflow_order(p_id);
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION kacrm_cashflow_payment_changed() RETURNS trigger AS $$
DECLARE old_id BIGINT; new_id BIGINT; old_amount NUMERIC := 0;
    new_amount NUMERIC := 0; p_id BIGINT; paid NUMERIC; delta NUMERIC;
BEGIN
    IF TG_OP<>'INSERT' THEN
        old_id := OLD.receivables_id;
        IF NOT OLD.deleted THEN old_amount := COALESCE(OLD.payamount,0); END IF;
    END IF;
    IF TG_OP<>'DELETE' THEN
        new_id := NEW.receivables_id;
        IF NOT NEW.deleted THEN new_amount := COALESCE(NEW.payamount,0); END IF;
    END IF;
    -- Lock both parents in a stable order, including moves between receivables.
    PERFORM 1 FROM receivables WHERE receivables_id=ANY(ARRAY[old_id,new_id])
      ORDER BY receivables_id FOR UPDATE;
    PERFORM 1 FROM salesorders WHERE salesorders_id IN
      (SELECT salesorders_id FROM receivables WHERE receivables_id=ANY(ARRAY[old_id,new_id]))
      ORDER BY salesorders_id FOR UPDATE;
    FOR p_id IN SELECT DISTINCT x FROM unnest(ARRAY[old_id,new_id]) x
      WHERE x IS NOT NULL ORDER BY x LOOP
        delta := CASE WHEN p_id=new_id THEN new_amount ELSE 0 END
               - CASE WHEN p_id=old_id THEN old_amount ELSE 0 END;
        SELECT CASE WHEN actualamount IS NULL THEN
            (SELECT COALESCE(SUM(payamount),0) FROM payments WHERE receivables_id=p_id AND NOT deleted)
          ELSE GREATEST(actualamount+delta,0) END INTO paid
          FROM receivables WHERE receivables_id=p_id;
        UPDATE receivables SET actualamount=paid,
          actualdate=CASE WHEN paid<=0 THEN NULL ELSE COALESCE(
            (SELECT MAX(paydate) FROM payments WHERE receivables_id=p_id AND NOT deleted),actualdate) END
          WHERE receivables_id=p_id;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION kacrm_cashflow_order_values() RETURNS trigger AS $$
BEGIN
    SELECT COALESCE(SUM(dueamount),0), COALESCE(SUM(actualamount),0), MAX(actualdate)::timestamptz
      INTO NEW.receivables_amount, NEW.payments_amount, NEW.latest_payment_date
      FROM receivables WHERE salesorders_id=NEW.salesorders_id AND NOT deleted;
    NEW.payment_status := CASE WHEN NEW.payments_amount<=0 THEN 'pending'
      WHEN NEW.payments_amount>=COALESCE(NEW.final_amount,NEW.receivables_amount) THEN 'received'
      ELSE 'partial' END;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER kacrm_receivable_values BEFORE INSERT OR UPDATE OF dueamount,actualamount,duedate
  ON receivables FOR EACH ROW EXECUTE FUNCTION kacrm_cashflow_receivable_values();
CREATE TRIGGER kacrm_receivable_changed AFTER INSERT OR UPDATE OF dueamount,actualamount,actualdate,salesorders_id,deleted OR DELETE
  ON receivables FOR EACH ROW EXECUTE FUNCTION kacrm_cashflow_receivable_changed();
CREATE TRIGGER kacrm_payment_changed AFTER INSERT OR UPDATE OF receivables_id,payamount,paydate,deleted OR DELETE
  ON payments FOR EACH ROW EXECUTE FUNCTION kacrm_cashflow_payment_changed();
CREATE TRIGGER kacrm_order_values BEFORE INSERT OR UPDATE OF final_amount,deleted
  ON salesorders FOR EACH ROW EXECUTE FUNCTION kacrm_cashflow_order_values();
'''


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(SQL)
    # Localized labels for the existing read-only order payment-status field.
    import sqlalchemy as sa
    op.get_bind().execute(sa.text("""UPDATE module_fields SET field_type='select', options=:options
      WHERE module_id IN (SELECT id FROM modules WHERE name='salesorders')
      AND field_name='payment_status' AND field_type='text' AND options IS NULL"""), {'options': PAYMENT_OPTIONS})


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for trigger, table in [('kacrm_order_values','salesorders'), ('kacrm_payment_changed','payments'),
                           ('kacrm_receivable_changed','receivables'), ('kacrm_receivable_values','receivables')]:
        op.execute(f'DROP TRIGGER {trigger} ON {table}')
    for function in ['kacrm_cashflow_order_values()', 'kacrm_cashflow_payment_changed()',
                     'kacrm_cashflow_receivable_changed()', 'kacrm_cashflow_receivable_values()',
                     'kacrm_cashflow_order(BIGINT)']:
        op.execute(f'DROP FUNCTION {function}')
    import sqlalchemy as sa
    op.get_bind().execute(sa.text("""UPDATE module_fields SET field_type='text', options=NULL
      WHERE module_id IN (SELECT id FROM modules WHERE name='salesorders')
      AND field_name='payment_status' AND options=:options"""), {'options': PAYMENT_OPTIONS})
