"""Upstream tables required by kacrm's full list query + CRUD port.

``list_query.py`` (the 1484-line one we're porting verbatim in Phase 1b.5b)
touches four tables that didn't exist in hermes_crm_dev yet:

  * ``record_collaborators`` — backs every ``shared_*`` list-query scope.
    Sharing a record is orthogonal to ownership: anyone with a row here +
    ``can_view = TRUE`` gains read on the referenced record.
  * ``products`` — the product catalog. ``list_query`` joins into this when
    a module has ``has_products`` metadata so product-line aggregates
    (``product_agg_quantity`` / ``product_agg_amount``) resolve. Phase 1
    doesn't yet ship a ``has_products`` module, but the JOIN code paths
    reference ``products.*`` directly so the table must exist for tests
    that exercise ``has_products`` paths.
  * ``currency_config`` + ``exchange_rates`` — currency normalization on
    ``currencyJson`` fields. Again, Phase 1 modules don't have currency
    fields yet, but the ported code reads these tables during aggregate
    rollup.

The kacrm ``product_agg_*`` names are NOT physical tables — they are
synthetic row keys computed in-memory from ``{module}_products`` line-item
tables (``salesorders_products``, ``quotes_products``, ...). Those line
tables land with Phase 2 when the parent modules (quotes, salesorders) do.

PG deviations from kacrm MySQL (same conventions used in 0001..0004):
  * ``tinyint(1)`` → ``BOOLEAN``; defaults preserved.
  * ``datetime`` / ``timestamp`` → ``TIMESTAMPTZ`` + ``set_updated_at()``
    trigger to replace ``ON UPDATE CURRENT_TIMESTAMP``.
  * ``decimal(15,6)`` → ``NUMERIC(15,6)``; ``int(19)`` / ``int(11)`` →
    ``BIGINT``.
  * FK discipline matches 0004: ``owner_by`` / ``created_by`` /
    ``updated_by`` → ``users(id) ON DELETE SET NULL`` so offboarding a user
    doesn't cascade-wipe catalog rows.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-15
"""
from __future__ import annotations

from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # record_collaborators — per-(module, record, user) sharing grant.
    # kacrm queries this from ``list_api_service._build_view_scope_where``
    # for the ``shared_to_me`` / ``shared_by_me`` / subordinate variants.
    #
    # The composite unique index ``(module_id, record_id, user_id)`` is
    # what kacrm uses to short-circuit re-sharing (same row → upsert).
    # We keep the same column order so ``INSERT ... ON CONFLICT`` clauses
    # port verbatim.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE record_collaborators (
            id          BIGSERIAL PRIMARY KEY,
            module_id   BIGINT NOT NULL
                        REFERENCES collections(id) ON DELETE CASCADE,
            record_id   BIGINT NOT NULL,
            user_id     BIGINT NOT NULL
                        REFERENCES users(id) ON DELETE CASCADE,
            can_view    BOOLEAN NOT NULL DEFAULT TRUE,
            can_edit    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (module_id, record_id, user_id)
        );
        """
    )
    # Lookup by (module_id, user_id) is the hot path — every list query for
    # a non-admin with a shared_* scope ends up here.
    op.execute(
        "CREATE INDEX idx_record_collaborators_user_module "
        "ON record_collaborators(user_id, module_id);"
    )

    # ------------------------------------------------------------------
    # products — kacrm's product catalog.
    #
    # The column set mirrors kacrm/docs/phpcrm_tenant_c3crm.sql lines
    # 2730-2758. Pruned nothing — even the denormalized ``is_top`` /
    # ``top_order`` UI pins are kept so the form renderer matches kacrm
    # shape byte-for-byte. ``price`` / ``reference_price`` / ``stock`` /
    # ``safe_stock`` were ``int(19)`` in MySQL (PHP doesn't care about
    # width); BIGINT is the honest PG mapping.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE products (
            products_id       BIGSERIAL PRIMARY KEY,
            products_name     VARCHAR(255) NOT NULL,
            deleted           BOOLEAN NOT NULL DEFAULT FALSE,
            owner_by          BIGINT REFERENCES users(id) ON DELETE SET NULL,
            department_id     BIGINT REFERENCES departments(id) ON DELETE SET NULL,
            created_by        BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by        BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

            cost_price        NUMERIC(10,2),
            price             BIGINT,
            description       TEXT,
            catalog           VARCHAR(250),
            productno         VARCHAR(50),
            image             VARCHAR(250),
            reference_price   BIGINT,
            stock             BIGINT NOT NULL DEFAULT 0,
            safe_stock        BIGINT NOT NULL DEFAULT 0,
            status            VARCHAR(250),
            model             VARCHAR(250),
            specifications    VARCHAR(250),

            is_top            BOOLEAN NOT NULL DEFAULT FALSE,
            top_order         INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX idx_products_owner ON products(owner_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_products_created_by ON products(created_by) WHERE deleted = FALSE;")
    op.execute("CREATE INDEX idx_products_alive ON products(products_id) WHERE deleted = FALSE;")
    op.execute(
        "CREATE TRIGGER trg_products_updated_at BEFORE UPDATE ON products "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # currency_config — currency registry.
    #
    # kacrm defaults include CNY, USD, EUR, JPY, GBP, HKD with CNY as the
    # is_default anchor. Seeding happens in ``seed.py`` so the migration
    # stays data-neutral (easier to rerun in tests without clobbering a
    # tenant's edits).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE currency_config (
            id              BIGSERIAL PRIMARY KEY,
            currency_code   VARCHAR(10) NOT NULL UNIQUE,
            currency_name   VARCHAR(50) NOT NULL,
            currency_symbol VARCHAR(10) NOT NULL,
            is_default      BOOLEAN NOT NULL DEFAULT FALSE,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE TRIGGER trg_currency_config_updated_at BEFORE UPDATE ON currency_config "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ------------------------------------------------------------------
    # exchange_rates — rate_to_default is the multiplier from this currency
    # back to the ``is_default = TRUE`` one in ``currency_config``.
    #
    # kacrm stores rates denormalized (no FK between the two tables) because
    # a rate entry can outlive its currency code during migrations. We keep
    # the same loose coupling — consistency is a seed-time invariant, not a
    # DB constraint.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE exchange_rates (
            id              BIGSERIAL PRIMARY KEY,
            currency_code   VARCHAR(10) NOT NULL UNIQUE,
            rate_to_default NUMERIC(15,6) NOT NULL,
            source          VARCHAR(20) NOT NULL DEFAULT 'manual',
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE TRIGGER trg_exchange_rates_updated_at BEFORE UPDATE ON exchange_rates "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS exchange_rates;")
    op.execute("DROP TABLE IF EXISTS currency_config;")
    op.execute("DROP TABLE IF EXISTS products;")
    op.execute("DROP TABLE IF EXISTS record_collaborators;")
