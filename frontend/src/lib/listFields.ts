/**
 * List-item field customization (Spec 1).
 *
 * Two-row layout config per module: ``{row1, row2}``. Resolution ladder:
 *   personal override (localStorage) → org default (backend list_fields)
 *   → built-in default here.
 *
 * Also exposes ``formatListFieldValue`` — the value formatter list rows use,
 * mirroring RecordDetail's renderFieldValue (display sibling → select label
 * → date / money / boolean → raw) without importing that component.
 */
import type { CrmFieldMeta, CrmRecord, ListFieldsConfig } from "./api";
import { selectValueLabel } from "./crmLabels";
import { enumLabel, type T } from "./i18n";

const NAME_KEYS = [
  "accounts_name", "contacts_name", "leads_name", "potentials_name",
  "account_name", "contact_name", "lead_name", "potential_name",
  "deal_name", "name", "subject", "title", "full_name",
];

function nameKeyFor(module: string): string {
  return `${module}_name`;
}

// Built-in defaults. row1 = primary title (the module name field); row2 =
// a sensible secondary set mirroring the legacy hardcoded badges.
export const DEFAULT_MODULE_LIST_FIELDS: Record<string, ListFieldsConfig> = {
  accounts: { row1: ["accounts_name"], row2: ["industry", "deal_amount", "status"] },
  contacts: { row1: ["contacts_name"], row2: ["mobile", "email", "owner_by"] },
  leads: { row1: ["leads_name"], row2: ["source", "status", "mobile"] },
  potentials: { row1: ["potential_name"], row2: ["amount", "milestone", "closingdate"] },
  quotes: { row1: ["subject"], row2: ["final_amount", "status", "quotedate"] },
  salesorders: { row1: ["subject"], row2: ["final_amount", "status", "orderdate"] },
  tasks: { row1: ["subject"], row2: ["priority", "status", "enddate"] },
  products: { row1: ["products_name"], row2: ["productno", "price", "stock"] },
  receivables: { row1: ["accounts_id"], row2: ["dueamount", "duedate", "receivablesstatus"] },
  payments: { row1: ["receivables_id"], row2: ["payamount", "paydate"] },
};

export function defaultListFields(module: string): ListFieldsConfig {
  return (
    DEFAULT_MODULE_LIST_FIELDS[module] || { row1: [nameKeyFor(module)], row2: [] }
  );
}

function lsKey(module: string): string {
  return `crm_list_fields_${module}`;
}

export function getPersonalListFields(module: string): ListFieldsConfig | null {
  try {
    const raw = localStorage.getItem(lsKey(module));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && Array.isArray(parsed.row1) && Array.isArray(parsed.row2)) {
      return { row1: parsed.row1, row2: parsed.row2 };
    }
  } catch { /* ignore malformed */ }
  return null;
}

export function setPersonalListFields(module: string, cfg: ListFieldsConfig): void {
  try {
    localStorage.setItem(lsKey(module), JSON.stringify(cfg));
  } catch { /* ignore quota */ }
}

export function clearPersonalListFields(module: string): void {
  try {
    localStorage.removeItem(lsKey(module));
  } catch { /* ignore */ }
}

/** Resolve effective config: personal → org → built-in default. */
export function resolveListFields(
  module: string,
  personal: ListFieldsConfig | null,
  orgDefault: ListFieldsConfig | null,
): ListFieldsConfig {
  if (personal && (personal.row1.length || personal.row2.length)) return personal;
  if (orgDefault && (orgDefault.row1.length || orgDefault.row2.length)) return orgDefault;
  return defaultListFields(module);
}

function fmtMoney(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? "");
  if (n >= 1_000_000) return `¥${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `¥${(n / 10_000).toFixed(1)}万`;
  return `¥${n.toFixed(0)}`;
}

function fmtDate(v: unknown): string {
  const d = new Date(String(v));
  if (Number.isNaN(d.getTime())) return String(v ?? "");
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const MONEY_FIELDS = new Set([
  "amount", "grand_total", "annual_revenue", "price", "total",
  "final_amount", "dueamount", "payamount", "actualamount", "balance",
  "deal_amount", "cost_price", "reference_price",
]);

/** Format one field's value for a list row. Returns "" when empty. */
export function formatListFieldValue(
  t: T, field: CrmFieldMeta, record: CrmRecord,
): string {
  const fn = field.field_name;
  // Unit prices and fractional amounts must stay exact; older API display
  // siblings round them to integers before they reach this formatter.
  const price = record[fn];
  const exactPrice = ["price", "cost_price", "reference_price"].includes(fn);
  const monetary = MONEY_FIELDS.has(fn) || ["currency", "money"].includes(field.field_type);
  if ((exactPrice || (monetary && !Number.isInteger(Number(price))))
      && price !== null && price !== undefined && price !== ""
      && Number.isFinite(Number(price))) {
    return `¥${Number(price).toLocaleString("en-US", { maximumFractionDigits: 20 })}`;
  }
  // FK display sibling takes precedence (backend-localized).
  const disp = record[fn + "_display"];
  if (disp !== undefined && disp !== null && String(disp).trim()) {
    return String(disp);
  }
  const raw = record[fn];
  if (raw === null || raw === undefined || raw === "") return "";
  switch (field.field_type) {
    case "datetime":
    case "date":
      return fmtDate(raw);
    case "currency":
    case "money":
      return fmtMoney(raw);
    case "number":
      return MONEY_FIELDS.has(fn) ? fmtMoney(raw) : String(raw);
    case "select":
    case "picklist":
    case "radio": {
      const m = selectValueLabel(raw, field.options, field.field_config);
      return m === String(raw) ? enumLabel(t, raw) : m;
    }
    case "boolean":
    case "checkbox":
      return raw ? t("common.yes") : t("common.no");
    default:
      return String(raw);
  }
}

/** Primary-title text from row1 fields; falls back to a name key, then #id. */
export function listPrimaryText(
  t: T,
  row1: string[],
  fieldMap: Record<string, CrmFieldMeta>,
  record: CrmRecord,
): string {
  const parts: string[] = [];
  for (const fn of row1) {
    const f = fieldMap[fn];
    if (!f) continue;
    const v = formatListFieldValue(t, f, record);
    if (v) parts.push(v);
  }
  if (parts.length) return parts.join(" · ");
  for (const k of NAME_KEYS) {
    if (record[k]) return String(record[k]);
  }
  return `#${record.id}`;
}
