/**
 * Per-module headline fields — the ≤5 business facts pinned to the top
 * of RecordDetail as the "one glance" chip bar.
 *
 * Charter §168 (新物种 ≠ 旧 CRM) — the old 13-field grid was a schema
 * dump; this is a business-language-first lens. When adding a module,
 * think: "what 5 things does a salesperson need to see without clicking
 * anything?"  Not "what columns exist."
 *
 * Names here must match `module_fields.field_name` in the DB. The
 * resolver falls back to the `*_display` sibling when present (user /
 * department / relation FK). Field names verified against live schema
 * 2026-04-18 — re-verify after future migrations.
 */
export const DEFAULT_MODULE_HEADLINES: Record<string, string[]> = {
  accounts:    ["industry", "deal_amount", "latest_contact_date", "owner_by", "status"],
  contacts:    ["accounts_id", "mobile", "email", "owner_by"],
  leads:       ["source", "status", "mobile", "owner_by"],
  potentials:  ["accounts_id", "amount", "milestone", "closingdate", "owner_by"],
  quotes:      ["accounts_id", "final_amount", "quotedate", "status", "owner_by"],
  salesorders: ["accounts_id", "final_amount", "orderdate", "status", "owner_by"],
  tasks:       ["priority", "status", "enddate", "accounts_id", "owner_by"],
  products:    ["productno", "price", "stock", "status"],
  receivables: ["accounts_id", "dueamount", "duedate", "receivablesstatus", "owner_by"],
  payments:    ["receivables_id", "payamount", "paydate", "owner_by"],
};

/**
 * Display labels for headline slots — business-facing overrides for DB
 * labels that are still English (Quote Name, Status, …) or that we want
 * tighter than the raw field_label. Values are i18n dict keys; resolve
 * via `headlineLabelOverride(fieldName, t)`. Missing key ⇒ fall back to
 * `field_label` from metadata.
 */
import type { T, DictKey } from "./i18n";

export const HEADLINE_LABEL_OVERRIDE_KEYS: Record<string, DictKey> = {
  owner_by: "headline.owner_by",
  accounts_id: "headline.accounts_id",
  contacts_id: "headline.contacts_id",
  receivables_id: "headline.receivables_id",
  status: "headline.status",
  priority: "headline.priority",
  milestone: "headline.milestone",
  source: "headline.source",
  industry: "headline.industry",
  mobile: "headline.mobile",
  email: "headline.email",
  amount: "headline.amount",
  final_amount: "headline.final_amount",
  dueamount: "headline.dueamount",
  payamount: "headline.payamount",
  price: "headline.price",
  stock: "headline.stock",
  productno: "headline.productno",
  quotedate: "headline.quotedate",
  orderdate: "headline.orderdate",
  closingdate: "headline.closingdate",
  duedate: "headline.duedate",
  paydate: "headline.paydate",
  enddate: "headline.enddate",
  latest_contact_date: "headline.latest_contact_date",
  deal_amount: "headline.deal_amount",
  receivablesstatus: "headline.receivablesstatus",
};

/** Resolve an i18n-translated override for ``fieldName``, or null. */
export function headlineLabelOverride(fieldName: string, t: T): string | null {
  const k = HEADLINE_LABEL_OVERRIDE_KEYS[fieldName];
  return k ? t(k) : null;
}

/** Legacy alias — kept for existing imports. Prefer ``resolveHeadlineFields``. */
export const MODULE_HEADLINES = DEFAULT_MODULE_HEADLINES;

export function headlineFieldsFor(module: string): string[] {
  return DEFAULT_MODULE_HEADLINES[module] || [];
}

/**
 * Resolve the chip-row field list for a module.
 *
 * ``override`` is the admin-curated ``collections.headline_fields`` value
 * (migration 0020) — a non-empty string array wins; ``null`` / ``undefined``
 * / empty array falls back to the frontend default above.
 *
 * This is intentionally lenient — a malformed override never breaks the
 * chip row, it just degrades to the default.
 */
/**
 * Resolve the effective headline chip fields for a module.
 * Priority: personal override (localStorage) → org default (backend
 * ``headline_fields``) → built-in default. Field count is uncapped.
 */
export function resolveHeadlineFields(
  module: string,
  orgOverride?: string[] | null,
  personalOverride?: string[] | null,
): string[] {
  const clean = (a: unknown): string[] =>
    Array.isArray(a) ? a.filter((f): f is string => typeof f === "string" && !!f.trim()) : [];
  const personal = clean(personalOverride);
  if (personal.length) return personal;
  const org = clean(orgOverride);
  if (org.length) return org;
  return DEFAULT_MODULE_HEADLINES[module] || [];
}

// ── Personal (per-user) headline override — localStorage, everyone ──────
function headlineLsKey(module: string): string {
  return `crm_headline_fields_${module}`;
}
export function getPersonalHeadline(module: string): string[] | null {
  try {
    const raw = localStorage.getItem(headlineLsKey(module));
    if (!raw) return null;
    const a = JSON.parse(raw);
    return Array.isArray(a) ? a.filter((x: unknown): x is string => typeof x === "string") : null;
  } catch { return null; }
}
export function setPersonalHeadline(module: string, fields: string[]): void {
  try { localStorage.setItem(headlineLsKey(module), JSON.stringify(fields)); } catch { /* ignore */ }
}
export function clearPersonalHeadline(module: string): void {
  try { localStorage.removeItem(headlineLsKey(module)); } catch { /* ignore */ }
}
