/**
 * Select-like field-type predicates — the single source of truth for
 * "does this field render as a dropdown / checkbox group".
 *
 * Canonical types are ``picklist`` (single) and ``multiselect`` (multi),
 * per ``hermes_cloud/app/crm/field_provisioning.FIELD_TYPE_ALIASES``.
 * The legacy aliases (select/radio/enum/option, multipicklist) must keep
 * matching forever: alembic 0107 renamed the DB metadata, but payloads
 * from older servers and un-migrated tenants can still carry them.
 *
 * Every renderer MUST branch through these predicates instead of
 * hand-rolling ``ft === "select" || ...`` — the 2026-07 建档表单 bug was
 * exactly such a hand-rolled list going stale when 0107 landed.
 */

const PICKLIST_TYPES = new Set(["picklist", "select", "enum", "option", "radio"]);
const MULTISELECT_TYPES = new Set(["multiselect", "multipicklist"]);

/** Single-choice dropdown (canonical ``picklist`` + legacy aliases). */
export function isPicklistLike(fieldType: string | null | undefined): boolean {
  return PICKLIST_TYPES.has((fieldType || "").trim().toLowerCase());
}

/** Multi-choice checkbox group (canonical ``multiselect`` + legacy alias). */
export function isMultiselectLike(fieldType: string | null | undefined): boolean {
  return MULTISELECT_TYPES.has((fieldType || "").trim().toLowerCase());
}

/** Any select-like field — value must come from the options list. */
export function isSelectLike(fieldType: string | null | undefined): boolean {
  return isPicklistLike(fieldType) || isMultiselectLike(fieldType);
}
