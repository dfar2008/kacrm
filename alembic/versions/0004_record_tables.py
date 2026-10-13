"""The three core CRM record tables: accounts, contacts, leads.

We keep kacrm's ``{module}_id`` PK naming (``accounts_id``, ``contacts_id``,
``leads_id``) so field-name parity with ``module_fields.field_name`` rows is
preserved — the list query builder joins them by name.

Columns pruned from the kacrm dump:
  * ``accounts.field_32``, ``ceshiziduan``, ``kehulaiyuan``, ``test``,
    ``multiselect`` — these are user-added custom fields / test scaffolding
    in the sample DB, not baseline business columns. Custom fields will come
    back via the ``field_save.py`` ALTER TABLE path in a later phase.

PG deviations preserved consistently with migration 0001 conventions:
  * ``tinyint(1)`` → ``BOOLEAN``. ``deleted`` defaults to FALSE.
  * ``datetime`` → ``TIMESTAMPTZ`` + ``set_updated_at()`` trigger.
  * ``decimal(18,2)`` → ``NUMERIC(18,2)``.
  * kacrm's ``contacts.is_main`` was ``varchar(250)`` storing Y/N; we migrate
    to ``BOOLEAN`` and treat absence as FALSE.

Ownership / FK strategy: every record row has ``owner_by``, ``created_by``,
``updated_by`` pointing at ``users(id)``. We use ``ON DELETE SET NULL`` so
deleting a user doesn't cascade-wipe their records — the CRM needs to retain
history even after HR offboards someone. ``department_id`` is FK to
``departments(id)``, same ``SET NULL`` discipline.

Cross-record FKs (``accounts.leads_id``, ``accounts.contacts_id``,
``contacts.accounts_id``) are intentionally NOT declared as DB-level foreign
keys. kacrm treats these as soft references (an account can survive the
deletion of its converted-from lead). DB-level FKs would force a deletion
order we don't want to enforce.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-15
"""
from __future__ import annotations

from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # accounts — the 账户/客户 table. Biggest of the three because it
    # carries the denormalized aggregate counters kacrm uses to avoid
    # computing them on every list load.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE accounts (
            accounts_id         BIGSERIAL PRIMARY KEY,
            accounts_name       VARCHAR(255) NOT NULL,
            owner_by            BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_by          BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by          BIGINT REFERENCES users(id) ON DELETE SET NULL,
            deleted             BOOLEAN NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- business descriptors
            industry            VARCHAR(255),
            contactdate         DATE,
            description         TEXT,
            parentaccount       BIGINT,                -- soft ref -> accounts.accounts_id
            department_id       BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            attachments         BIGINT,
            logo                BIGINT,
            richtext            TEXT,
            mobile              VARCHAR(20),
            email               VARCHAR(100),
            website             VARCHAR(255),
            accountno           VARCHAR(50),
            leads_id            BIGINT,                -- soft ref -> leads.leads_id

            -- approval workflow
            approval_status     VARCHAR(20),
            approval_user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
            approval_time       TIMESTAMPTZ,

            -- contact cadence tracking
            latest_contact_date DATE,
            latest_progress     TEXT,
            next_contact_date   DATE,
            contact_count       INTEGER NOT NULL DEFAULT 0,

            -- denormalized pipeline aggregates
            latest_deal_date    DATE,
            deal_count          INTEGER NOT NULL DEFAULT 0,
            deal_amount         NUMERIC(18,2) NOT NULL DEFAULT 0,
            opportunity_count   INTEGER NOT NULL DEFAULT 0,
            opportunity_amount  NUMERIC(18,2) NOT NULL DEFAULT 0,
            quote_count         INTEGER NOT NULL DEFAULT 0,
            quote_amount        NUMERIC(18,2) NOT NULL DEFAULT 0,
            receivables_amount  NUMERIC(18,2) NOT NULL DEFAULT 0,
            payments_amount     NUMERIC(18,2) NOT NULL DEFAULT 0,
            latest_payment_date TIMESTAMPTZ,
            invoice_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,

            -- UI sort pins + audit-adjacent
            is_top              BOOLEAN NOT NULL DEFAULT FALSE,
            top_order           INTEGER NOT NULL DEFAULT 0,
            latest_comment_time TIMESTAMPTZ,
            status              VARCHAR(250),
            contacts_id         BIGINT                 -- soft ref -> contacts.contacts_id
        );
        """
    )
    op.execute("CREATE INDEX idx_accounts_owner ON accounts(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_accounts_created_by ON accounts(created_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_accounts_department ON accounts(department_id) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_accounts_alive ON accounts(accounts_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_accounts_updated_at BEFORE UPDATE ON accounts "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # contacts — 联系人 belonging to an account.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE contacts (
            contacts_id     BIGSERIAL PRIMARY KEY,
            contacts_name   VARCHAR(255) NOT NULL,
            deleted         BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by        BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id   BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            accounts_id     BIGINT,                    -- soft ref -> accounts.accounts_id
            mobile          VARCHAR(20),
            email           VARCHAR(100),
            phone           VARCHAR(20),
            gender          VARCHAR(20),
            is_main         BOOLEAN NOT NULL DEFAULT FALSE,

            is_top          BOOLEAN NOT NULL DEFAULT FALSE,
            top_order       INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_contacts_owner ON contacts(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_contacts_created_by ON contacts(created_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_contacts_account ON contacts(accounts_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_contacts_updated_at BEFORE UPDATE ON contacts "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # leads — 线索 / pre-conversion records. ``convertedstatus`` tracks where
    # in the conversion flow the lead sits; ``convertedby`` / ``convertedat``
    # are audit breadcrumbs.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE leads (
            leads_id        BIGSERIAL PRIMARY KEY,
            leads_name      VARCHAR(255) NOT NULL,
            deleted         BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by        BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id   BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            phone           VARCHAR(20),
            mobile          VARCHAR(20),
            email           VARCHAR(100),
            status          VARCHAR(50),
            contact         VARCHAR(255),
            remarks         TEXT,
            source          VARCHAR(100),

            convertedstatus VARCHAR(50),
            convertedat     TIMESTAMPTZ,
            convertedby     BIGINT REFERENCES users(id) ON DELETE SET NULL,

            is_top          BOOLEAN NOT NULL DEFAULT FALSE,
            top_order       INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_leads_owner ON leads(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_leads_created_by ON leads(created_by) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_leads_updated_at BEFORE UPDATE ON leads "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS leads;")
    op.execute("DROP TABLE IF EXISTS contacts;")
    op.execute("DROP TABLE IF EXISTS accounts;")
