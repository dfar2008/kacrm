"""CRM business module tables: potentials, quotes, salesorders, tasks,
receivables, payments, and their product line-item tables.

Ported from kacrm/docs/phpcrm_tenant_c3crm.sql with the same MySQL → PG
conventions used in 0004/0005:

  * ``tinyint(1)`` → ``BOOLEAN``
  * ``datetime`` → ``TIMESTAMPTZ`` + ``set_updated_at()`` trigger
  * ``decimal(X,Y)`` → ``NUMERIC(X,Y)``
  * ``int(11)`` / ``int(19)`` → ``BIGINT``
  * JSON columns kept as ``JSONB`` (kacrm stores currency-aware amounts
    as ``{"CNY": 1000.00}`` objects)
  * Soft-reference columns (accounts_id, contacts_id, etc.) have NO
    DB-level FK — same discipline as 0004

Deprecated columns from kacrm migrations 017/021 (pipeline,
currency/currency_original_amount on potentials/salesorders) are omitted.
Test scaffolding columns (test_id, laiyuan, source on salesorders) are
also omitted.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-16
"""
from __future__ import annotations

from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # potentials — 商机 / sales opportunities
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE potentials (
            potentials_id           BIGSERIAL PRIMARY KEY,
            potentials_name         VARCHAR(255) NOT NULL,
            deleted                 BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by                BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id           BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

            potentialno             VARCHAR(50),
            accounts_id             BIGINT,          -- soft ref → accounts
            contacts_id             BIGINT,          -- soft ref → contacts
            closingdate             DATE,
            amount                  BIGINT,
            description             TEXT,
            probability             VARCHAR(250),
            milestone               VARCHAR(250),
            expected_close_date     DATE,
            actual_close_date       DATE,
            lost_reason             VARCHAR(250),
            status                  VARCHAR(250),

            is_top                  BOOLEAN NOT NULL DEFAULT FALSE,
            top_order               INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_potentials_owner ON potentials(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_potentials_accounts ON potentials(accounts_id) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_potentials_alive ON potentials(potentials_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_potentials_updated_at BEFORE UPDATE ON potentials "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # quotes — 报价
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE quotes (
            quotes_id               BIGSERIAL PRIMARY KEY,
            quotes_name             VARCHAR(255) NOT NULL,
            deleted                 BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by                BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id           BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

            accounts_id             BIGINT,          -- soft ref → accounts
            contacts_id             BIGINT,          -- soft ref → contacts
            potentials_id           BIGINT,          -- soft ref → potentials
            status                  VARCHAR(250),
            quotedate               DATE,
            total_quantity          INTEGER NOT NULL DEFAULT 0,
            total_tax               JSONB,
            total_with_tax          JSONB,
            adjustment_amount       JSONB,
            adjustment_remark       TEXT,
            final_amount            JSONB,
            product_currency        JSONB,

            is_top                  BOOLEAN NOT NULL DEFAULT FALSE,
            top_order               INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_quotes_owner ON quotes(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_quotes_accounts ON quotes(accounts_id) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_quotes_alive ON quotes(quotes_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_quotes_updated_at BEFORE UPDATE ON quotes "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # quotes_products — 报价产品明细
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE quotes_products (
            quotes_products_id      BIGSERIAL PRIMARY KEY,
            quotes_id               BIGINT NOT NULL,
            products_id             BIGINT NOT NULL,
            productno               VARCHAR(50) NOT NULL,
            products_name           VARCHAR(255) NOT NULL,
            model                   VARCHAR(50) NOT NULL DEFAULT '',
            specifications          VARCHAR(50) NOT NULL DEFAULT '',
            tax_rate                NUMERIC(19,2) NOT NULL DEFAULT 0,
            tax_amount              JSONB,
            quantity                INTEGER NOT NULL DEFAULT 0,
            price                   JSONB,
            cost_price              NUMERIC(10,2),
            subtotal                JSONB,
            total                   JSONB,
            remark                  TEXT,
            discount_rate           NUMERIC(5,2) DEFAULT 0,
            discount_price          JSONB,
            tax_after_price         JSONB,
            reference_id            BIGINT,
            reference_products_id   BIGINT,
            reference_type          VARCHAR(20),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_quotes_products_quote ON quotes_products(quotes_id);")
    op.execute("CREATE INDEX idx_quotes_products_product ON quotes_products(products_id);")
    op.execute(
        "CREATE TRIGGER trg_quotes_products_updated_at BEFORE UPDATE ON quotes_products "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # salesorders — 合同/订单
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE salesorders (
            salesorders_id          BIGSERIAL PRIMARY KEY,
            salesorders_name        VARCHAR(255) NOT NULL,
            deleted                 BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by                BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id           BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

            status                  VARCHAR(250),
            ordertype               VARCHAR(250),
            terms                   TEXT,
            description             TEXT,
            orderdate               DATE,
            accounts_id             BIGINT,          -- soft ref → accounts
            contacts_id             BIGINT,          -- soft ref → contacts
            quotes_id               BIGINT,          -- soft ref → quotes
            potentials_id           BIGINT,          -- soft ref → potentials
            total_quantity          NUMERIC(19,2) NOT NULL DEFAULT 0,
            total_tax               JSONB,
            total_with_tax          JSONB,
            adjustment_amount       JSONB,
            adjustment_remark       VARCHAR(255),
            final_amount            JSONB,
            product_currency        JSONB,
            invoicestatus           VARCHAR(250),
            payment_status          VARCHAR(50),
            warehouse_status        VARCHAR(20),
            approval_status         VARCHAR(20),
            approval_user_id        BIGINT REFERENCES users(id) ON DELETE SET NULL,
            approval_time           TIMESTAMPTZ,
            receivables_amount      JSONB,
            payments_amount         JSONB,
            latest_payment_date     TIMESTAMPTZ,
            invoice_amount          JSONB,
            latest_comment_time     TIMESTAMPTZ,

            is_top                  BOOLEAN NOT NULL DEFAULT FALSE,
            top_order               INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_salesorders_owner ON salesorders(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_salesorders_accounts ON salesorders(accounts_id) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_salesorders_alive ON salesorders(salesorders_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_salesorders_updated_at BEFORE UPDATE ON salesorders "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # salesorders_products — 订单产品明细
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE salesorders_products (
            salesorders_products_id BIGSERIAL PRIMARY KEY,
            salesorders_id          BIGINT NOT NULL,
            products_id             BIGINT NOT NULL,
            productno               VARCHAR(50) NOT NULL,
            products_name           VARCHAR(255) NOT NULL,
            model                   VARCHAR(50) NOT NULL DEFAULT '',
            specifications          VARCHAR(50) NOT NULL DEFAULT '',
            tax_rate                NUMERIC(10,2) NOT NULL DEFAULT 0,
            tax_amount              JSONB,
            quantity                INTEGER NOT NULL DEFAULT 0,
            price                   JSONB,
            cost_price              NUMERIC(10,2),
            subtotal                JSONB,
            total                   JSONB,
            remark                  TEXT,
            discount_rate           NUMERIC(5,2) DEFAULT 0,
            discount_price          JSONB,
            tax_after_price         JSONB,
            reference_id            BIGINT,
            reference_products_id   BIGINT,
            reference_type          VARCHAR(20),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_salesorders_products_order ON salesorders_products(salesorders_id);")
    op.execute("CREATE INDEX idx_salesorders_products_product ON salesorders_products(products_id);")
    op.execute(
        "CREATE TRIGGER trg_salesorders_products_updated_at BEFORE UPDATE ON salesorders_products "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # tasks — 任务
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE tasks (
            tasks_id                BIGSERIAL PRIMARY KEY,
            tasks_name              VARCHAR(255) NOT NULL,
            deleted                 BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by                BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id           BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

            description             TEXT,
            status                  VARCHAR(250),
            priority                VARCHAR(250),
            startdate               DATE,
            enddate                 DATE,
            accounts_id             BIGINT,          -- soft ref → accounts
            contacts_id             BIGINT,          -- soft ref → contacts

            is_top                  BOOLEAN NOT NULL DEFAULT FALSE,
            top_order               INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_tasks_owner ON tasks(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_tasks_alive ON tasks(tasks_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_tasks_updated_at BEFORE UPDATE ON tasks "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # receivables — 应收款
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE receivables (
            receivables_id          BIGSERIAL PRIMARY KEY,
            receivables_name        VARCHAR(255) NOT NULL,
            deleted                 BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by                BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id           BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

            accounts_id             BIGINT,          -- soft ref → accounts
            contacts_id             BIGINT,          -- soft ref → contacts
            salesorders_id          BIGINT,          -- soft ref → salesorders
            dueamount               JSONB,
            actualamount            JSONB,
            duedate                 DATE,
            actualdate              DATE,
            receivablesstatus       VARCHAR(250),
            accountingitem          VARCHAR(250),
            balance                 JSONB,
            product_currency        JSONB,

            is_top                  BOOLEAN NOT NULL DEFAULT FALSE,
            top_order               INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_receivables_owner ON receivables(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_receivables_salesorder ON receivables(salesorders_id) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_receivables_alive ON receivables(receivables_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_receivables_updated_at BEFORE UPDATE ON receivables "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # payments — 回款
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE payments (
            payments_id             BIGSERIAL PRIMARY KEY,
            payments_name           VARCHAR(255) NOT NULL,
            deleted                 BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by                BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id           BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

            paydate                 DATE,
            payamount               JSONB,
            remark                  TEXT,
            bankaccount_id          BIGINT,
            receivables_id          BIGINT,          -- soft ref → receivables
            product_currency        JSONB,

            is_top                  BOOLEAN NOT NULL DEFAULT FALSE,
            top_order               INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_payments_owner ON payments(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_payments_receivable ON payments(receivables_id) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_payments_alive ON payments(payments_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_payments_updated_at BEFORE UPDATE ON payments "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payments;")
    op.execute("DROP TABLE IF EXISTS receivables;")
    op.execute("DROP TABLE IF EXISTS tasks;")
    op.execute("DROP TABLE IF EXISTS salesorders_products;")
    op.execute("DROP TABLE IF EXISTS salesorders;")
    op.execute("DROP TABLE IF EXISTS quotes_products;")
    op.execute("DROP TABLE IF EXISTS quotes;")
    op.execute("DROP TABLE IF EXISTS potentials;")
