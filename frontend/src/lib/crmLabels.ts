import type { CrmFieldMeta } from "@/lib/api";
import type { T } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n";

/**
 * Resolve a human-readable field label.
 *
 * Field labels live in the ``module_fields`` table and ship in English
 * by default ("Account Name", "Approval Status", …). We layer i18n on
 * top WITHOUT touching the DB by trying these keys in order:
 *
 *   1. ``crm.field.<module>.<field_name>`` — per-module override
 *   2. ``crm.field.<field_name>``           — generic system field
 *
 * Anything that misses both falls back to the raw ``field_label`` so
 * admin-created custom fields keep working unchanged.
 */
export function fieldLabel(t: T, field: CrmFieldMeta, module?: string): string {
  const fn = field.field_name;
  if (module) {
    const k1 = `crm.field.${module}.${fn}`;
    const v1 = t(k1);
    if (v1 && v1 !== k1) return v1;
  }
  const k2 = `crm.field.${fn}`;
  const v2 = t(k2);
  if (v2 && v2 !== k2) return v2;
  return field.field_label || fn;
}

/**
 * Resolve a field label for a specific UI locale.
 *
 * Unlike ``fieldLabel`` (which walks the static i18n dictionary by key),
 * this reads the per-field, admin-authored translations stored in
 * ``field_config.label_i18n``. ``field_label`` is the canonical label
 * (currently authored in zh-CN) and is the fallback when the active locale
 * has no override:
 *
 *   field_config.label_i18n?.[locale]  →  field_label
 *
 * Legacy fields have ``field_config`` NULL → we fall straight through to
 * ``field_label``, so existing fields keep rendering unchanged.
 */
export function resolveFieldLabel(field: CrmFieldMeta, locale: Locale): string {
  return field.field_config?.label_i18n?.[locale] || field.field_label;
}

/** A picklist option pair — ``value`` is stored, ``text`` is displayed. */
export interface SelectOption { value: string; text: string; }

/**
 * Parse a field's ``options`` metadata into {value,text} pairs.
 *
 * Tolerates every on-disk / on-wire shape:
 *   - CSV of bare values:           "new,contacted,lost"
 *   - JSON array of strings:        '["new","contacted"]'
 *   - JSON array of {value,text}:   '[{"value":"new","text":"新建"}]'
 *
 * The backend localizes options before sending, so ``text`` already
 * carries the request-locale label (科技 / Technology).
 */
export function parseSelectOptions(raw: unknown): SelectOption[] {
  const norm = (arr: unknown[]): SelectOption[] =>
    arr.map(it => {
      if (it && typeof it === "object") {
        const o = it as Record<string, unknown>;
        const value = String(o.value ?? o.text ?? "").trim();
        const text = String(o.text ?? o.value ?? "").trim();
        return { value, text: text || value };
      }
      const s = String(it ?? "").trim();
      return { value: s, text: s };
    }).filter(o => o.value);

  if (Array.isArray(raw)) return norm(raw);
  if (typeof raw === "string") {
    const s = raw.trim();
    if (!s) return [];
    if (s[0] === "[") {
      try {
        const parsed = JSON.parse(s);
        return Array.isArray(parsed) ? norm(parsed) : [];
      } catch { return []; }
    }
    return s.split(",").map(x => x.trim()).filter(Boolean)
      .map(v => ({ value: v, text: v }));
  }
  return [];
}

/** Map a stored select value (or comma-joined multiselect values) to its
 *  localized option label(s); unknown values pass through unchanged.
 *
 *  Pass ``fieldConfig`` + ``preferLocale`` to honour
 *  ``field_config.option_labels`` overrides — required when the backend
 *  ships ``options`` as raw CSV (current shape) and labels live in
 *  ``field_config``. Without them the function falls back to whatever
 *  ``parseSelectOptions`` could extract from ``options`` alone (which
 *  for CSV input is value-as-text). */
export function selectValueLabel(
  raw: unknown,
  options: unknown,
  fieldConfig?: unknown,
  preferLocale?: string,
): string {
  const s = raw === null || raw === undefined ? "" : String(raw);
  if (!s.trim()) return s;
  const opts = fieldConfig !== undefined
    ? localizedSelectOptions(options, fieldConfig, preferLocale)
    : parseSelectOptions(options);
  if (!opts.length) return s;
  return s.split(",").map(p => p.trim()).filter(Boolean).map(p => {
    const hit = opts.find(o => o.value === p);
    return hit ? hit.text : p;
  }).join(", ") || s;
}

/**
 * Look up the localized display label for a picklist ``value`` from a
 * field's ``field_config.option_labels``.
 *
 * The on-disk shape we maintain is::
 *
 *     { "won": { "zh-CN": "赢单", "en-US": "Won" }, ... }
 *
 * Legacy flat-string entries (``{ "won": "赢单" }``) are still tolerated
 * — they're read as a zh-CN value (matches DEFAULT_LOCALE). Resolution
 * order: requested locale → zh-CN → en-US → undefined (caller decides
 * the fallback, usually the value itself).
 *
 * Centralised here so every consumer (AllInfoDrawer, CreateRecordModal,
 * FieldProperties, …) reads the same shape and the SPA stays consistent
 * with whatever the backend / admin UI just wrote.
 */
export function extractOptionLabel(
  fieldConfig: unknown, value: string, preferLocale?: string,
): string | undefined {
  if (!fieldConfig || typeof fieldConfig !== "object") return undefined;
  const labels = (fieldConfig as Record<string, unknown>).option_labels;
  if (!labels || typeof labels !== "object") return undefined;
  const entry = (labels as Record<string, unknown>)[value];
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object") {
    const map = entry as Record<string, unknown>;
    const order = [preferLocale, "zh-CN", "en-US"].filter(Boolean) as string[];
    for (const loc of order) {
      if (typeof map[loc] === "string") return map[loc] as string;
    }
  }
  return undefined;
}

/**
 * Combine raw CSV/JSON options with a field's ``field_config.option_labels``
 * to produce ``[{value, text}]`` pairs where ``text`` is the locale-aware
 * display label. Falls back to whatever ``parseSelectOptions`` returned
 * (which is value-as-text) when no label override exists.
 *
 * Call this from any form/list component rendering a picklist; passing
 * ``fieldConfig`` + ``locale`` is what makes Chinese labels show in zh-CN
 * and English in en-US without the backend needing to pre-localize the
 * ``options`` string per request.
 */
export function localizedSelectOptions(
  raw: unknown, fieldConfig: unknown, preferLocale?: string,
): SelectOption[] {
  const opts = parseSelectOptions(raw);
  if (!opts.length) return opts;
  return opts.map(o => {
    const label = extractOptionLabel(fieldConfig, o.value, preferLocale);
    return label ? { value: o.value, text: label } : o;
  });
}

/**
 * Resolve a module's display name. Backed by the cache from
 * ``listModules()`` — caller passes that result here so we don't fire
 * a fresh request per render. Falls back to the raw module slug if
 * the cache hasn't loaded yet OR the module is unknown.
 */
export function moduleLabel(
  module: string,
  cache: { name: string; label: string }[] | null | undefined,
): string {
  if (!module) return "";
  const hit = (cache || []).find(m => m.name === module);
  return hit?.label || module;
}
