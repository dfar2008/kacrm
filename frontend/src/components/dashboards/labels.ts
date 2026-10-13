/**
 * Column-name → user-facing label resolver.
 *
 * The backend's chart payload + drill rows return raw entity column names
 * (id / name / department_id / occurred_at / etc.). This function maps
 * each known column to its localized label via i18n; unknown columns
 * (custom user-added fields) fall back to the raw name. A future pass
 * can extend this to consult module_fields.field_label for live custom
 * labels — for now the raw name is acceptable since admins set custom
 * field_name in snake_case anyway.
 */
import type { T } from "@/lib/i18n";

export function colLabel(t: T, column: string): string {
  if (!column) return "";
  const key = `dashboards.col.${column}`;
  const translated = t(key);
  // useT returns the key itself when missing — treat that as no translation.
  return translated && translated !== key ? translated : column;
}

/** Map a raw enum value (e.g. milestone="initial_contact") to its
 * localized display label. Falls back to the raw value when no
 * translation is registered — keeps custom / user-defined values
 * legible without needing dict updates. */
export function valueLabel(t: T, column: string, raw: any): string {
  if (raw == null) return "";
  const key = `dashboards.value.${column}.${raw}`;
  const translated = t(key);
  return translated && translated !== key ? translated : String(raw);
}
