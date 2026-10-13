"""Unify CRM records with the memory identity layer.

Every CRM business record (account/contact/lead/potential) points at
exactly one ``memory_entities`` row. This is the charter's "one real
thing has one identity in the system" rule — we stop maintaining two
parallel universes (CRM account vs memory_entity) for the same ACME.

Scope:
  * ``accounts.memory_entity_id``    → ``org`` entity,    UNIQUE (1:1)
  * ``contacts.memory_entity_id``    → ``person`` entity, UNIQUE (1:1)
  * ``leads.memory_entity_id``       → ``lead`` entity,   UNIQUE (1:1)
  * ``potentials.memory_entity_id``  → ``deal`` entity,   non-unique
      (a memory_entity for a company can own many deals — potentials
      are deals attached to the *same* account, not new identities)

Backfill policy (per CRM row, ordered by id ASC):

  1. Try to match an existing memory_entities row at org-scope
     (``user_id IS NULL``) by ``lower(canonical) = lower(name)`` or
     case-insensitive alias match. The matched entity must not already
     be claimed by another live CRM row of the same table — this is
     how we preserve the 1:1 invariant in the face of duplicate-named
     CRM accounts (common in dirty live data: "测试公司" ×3).
  2. No usable match → insert a new memory_entities row. If the
     canonical collides with an existing one (unique index), fall back
     to a disambiguated canonical ``"<name> (#<crm_id>)"``.
  3. Store a provenance breadcrumb (``attributes.source =
     crm_backfill`` plus table / id) so operators can later audit
     which entities were system-created vs LLM-extracted.

NOT NULL on ``memory_entity_id`` is deferred — new write paths route
all CRM inserts through ``memory.entities.upsert_entity`` first. After
that ships we promote the column to NOT NULL in a follow-up revision.

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-18
"""
from __future__ import annotations

from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


# (crm_table, crm_pk, crm_name_col, memory_entity_type, enforce_unique)
_LINKS = [
    ("accounts",   "accounts_id",   "accounts_name",   "org",    True),
    ("contacts",   "contacts_id",   "contacts_name",   "person", True),
    ("leads",      "leads_id",      "leads_name",      "lead",   True),
    ("potentials", "potentials_id", "potentials_name", "deal",   False),
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Schema: nullable FK + indexes on each CRM table.
    # ------------------------------------------------------------------
    for table, _pk, _name, _etype, unique in _LINKS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN memory_entity_id BIGINT "
            f"REFERENCES memory_entities(id) ON DELETE SET NULL"
        )
        op.execute(
            f"CREATE INDEX idx_{table}_memory_entity "
            f"ON {table}(memory_entity_id) "
            f"WHERE memory_entity_id IS NOT NULL"
        )

    # ------------------------------------------------------------------
    # 2. Backfill — per-row loop, PL/pgSQL. See module docstring.
    # ------------------------------------------------------------------
    for table, pk, name_col, etype, _ in _LINKS:
        op.execute(f"""
        DO $$
        DECLARE
            r RECORD;
            eid BIGINT;
            disambig TEXT;
        BEGIN
            FOR r IN
                SELECT {pk} AS rid, {name_col} AS rname
                FROM {table}
                WHERE deleted = FALSE
                  AND memory_entity_id IS NULL
                  AND {name_col} IS NOT NULL
                  AND btrim({name_col}) <> ''
                ORDER BY {pk} ASC
            LOOP
                -- 2a. Match an existing entity NOT already claimed.
                SELECT e.id INTO eid
                FROM memory_entities e
                WHERE e.entity_type = '{etype}'
                  AND e.user_id IS NULL
                  AND (
                    lower(e.canonical) = lower(r.rname)
                    OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(e.aliases) a
                        WHERE lower(a) = lower(r.rname)
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM {table} t2
                    WHERE t2.memory_entity_id = e.id
                      AND t2.deleted = FALSE
                      AND t2.{pk} <> r.rid
                  )
                ORDER BY e.id ASC
                LIMIT 1;

                -- 2b. No match → insert, disambiguating on conflict.
                IF eid IS NULL THEN
                    BEGIN
                        INSERT INTO memory_entities
                            (entity_type, canonical, aliases, attributes,
                             user_id, confidence)
                        VALUES ('{etype}', r.rname, '[]'::jsonb,
                                jsonb_build_object(
                                    'source', 'crm_backfill',
                                    'crm_table', '{table}',
                                    'crm_id', r.rid
                                ),
                                NULL, 1.0)
                        RETURNING id INTO eid;
                    EXCEPTION WHEN unique_violation THEN
                        disambig := r.rname || ' (#' || r.rid || ')';
                        INSERT INTO memory_entities
                            (entity_type, canonical, aliases, attributes,
                             user_id, confidence)
                        VALUES ('{etype}', disambig,
                                jsonb_build_array(r.rname),
                                jsonb_build_object(
                                    'source', 'crm_backfill',
                                    'crm_table', '{table}',
                                    'crm_id', r.rid,
                                    'disambiguated', true
                                ),
                                NULL, 1.0)
                        RETURNING id INTO eid;
                    END;
                END IF;

                UPDATE {table}
                SET memory_entity_id = eid
                WHERE {pk} = r.rid;
            END LOOP;
        END $$;
        """)

    # ------------------------------------------------------------------
    # 3. Enforce 1:1 after backfill. Partial unique indexes so deleted
    # rows (tombstones) don't block re-use of freed entity ids.
    # ------------------------------------------------------------------
    for table, _pk, _name, _etype, unique in _LINKS:
        if unique:
            op.execute(
                f"CREATE UNIQUE INDEX uniq_{table}_memory_entity "
                f"ON {table}(memory_entity_id) "
                f"WHERE memory_entity_id IS NOT NULL AND deleted = FALSE"
            )


def downgrade() -> None:
    for table, _pk, _name, _etype, unique in reversed(_LINKS):
        if unique:
            op.execute(f"DROP INDEX IF EXISTS uniq_{table}_memory_entity")
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_memory_entity")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS memory_entity_id")
