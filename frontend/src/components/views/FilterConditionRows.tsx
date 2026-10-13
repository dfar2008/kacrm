/**
 * FilterConditionRows — the shared filter-condition builder used by both
 * ViewBuilder (saved views) and AdvancedSearch (ad-hoc list filtering).
 *
 * Renders a header with an all/any logic toggle, a list of condition rows
 * (field + operator + value-kind + value), and an "+ add condition" button.
 * The value input adapts to the field type (select → dropdown) and to a
 * dynamic "value kind" (current user / my team / today / this week /
 * last N days) that serializes to a magic string the backend resolves.
 */
import type { CrmFieldMeta } from "@/lib/api";
import { fieldLabel, localizedSelectOptions } from "@/lib/crmLabels";
import { useT, type T } from "@/lib/i18n";

// ── Operators ─────────────────────────────────────────────────────────
export const OPERATORS = [
  "eq", "neq", "gt", "gte", "lt", "lte", "in", "like",
  "contains", "not_contains", "starts_with", "ends_with",
  "is_empty", "is_not_empty",
] as const;

export const NO_VALUE_OPS = new Set(["is_empty", "is_not_empty"]);

// ── Dynamic value kinds ───────────────────────────────────────────────
export type DynKind = "literal" | "current" | "today" | "this_week" | "last_n_days" | "team";

// Fixed system fields that are always filterable even if not in module_fields.
export const SYSTEM_FILTER_FIELDS = [
  "owner_by", "created_by", "created_at", "latest_contact_date", "next_contact_date",
];

const USER_FIELD_NAMES = new Set(["owner_by", "created_by"]);
const DATE_FIELD_NAMES = new Set(["created_at", "latest_contact_date", "next_contact_date"]);

/** Dynamic value kinds that make sense for a given field. "current"/"team"
 *  only apply to user/owner fields; the date tokens only to date fields;
 *  every field allows a literal value. */
export function dynKindsForField(fieldName: string, field?: CrmFieldMeta): DynKind[] {
  const out: DynKind[] = ["literal"];
  const ft = field?.field_type;
  const userish = ft === "user" || ft === "createdBy" || USER_FIELD_NAMES.has(fieldName);
  const dateish = ft === "date" || ft === "datetime" || DATE_FIELD_NAMES.has(fieldName);
  if (userish) out.push("current", "team");
  if (dateish) out.push("today", "this_week", "last_n_days");
  return out;
}

export interface RowState {
  field: string;
  operator: string;
  dynKind: DynKind;
  /** Literal value (for dynKind==="literal") OR the N for last_n_days. */
  value: string;
}

export function newRow(): RowState {
  return { field: "", operator: "eq", dynKind: "literal", value: "" };
}

/** Reverse-parse a stored filter value into a row's {dynKind, value}. */
export function decodeValue(raw: unknown): { dynKind: DynKind; value: string } {
  if (raw === null || raw === undefined) return { dynKind: "literal", value: "" };
  const s = String(raw);
  if (s === "current" || s === "team" || s === "today" || s === "this_week") {
    return { dynKind: s as DynKind, value: "" };
  }
  if (s.startsWith("last_n_days:")) {
    return { dynKind: "last_n_days", value: s.slice("last_n_days:".length) };
  }
  return { dynKind: "literal", value: s };
}

/** Build the stored filter value for a row (literal/text branch only —
 *  is_empty/is_not_empty are handled by the caller as null). */
export function encodeValue(row: RowState): unknown {
  switch (row.dynKind) {
    case "current":
    case "team":
    case "today":
    case "this_week":
      return row.dynKind;
    case "last_n_days":
      return `last_n_days:${row.value.trim()}`;
    case "literal":
    default:
      return row.value;
  }
}

/** Candidate fields for the field dropdown = visible module fields + the
 *  fixed system fields. */
export function buildCandidateFields(
  t: T, fields: CrmFieldMeta[], module: string,
): { value: string; label: string; field?: CrmFieldMeta }[] {
  const out: { value: string; label: string; field?: CrmFieldMeta }[] = [];
  const seen = new Set<string>();
  for (const f of fields) {
    if (f.is_hidden) continue;
    if (seen.has(f.field_name)) continue;
    seen.add(f.field_name);
    out.push({ value: f.field_name, label: fieldLabel(t, f, module), field: f });
  }
  for (const sys of SYSTEM_FILTER_FIELDS) {
    if (seen.has(sys)) continue;
    seen.add(sys);
    const k = `crm.field.${sys}`;
    const v = t(k);
    out.push({ value: sys, label: v && v !== k ? v : sys });
  }
  return out;
}

interface Props {
  fields: CrmFieldMeta[];
  module: string;
  rows: RowState[];
  onChange: (rows: RowState[]) => void;
  logic: "all" | "any";
  onLogicChange: (logic: "all" | "any") => void;
}

const inputStyle: React.CSSProperties = {
  padding: "6px 8px",
  border: "1px solid var(--border)", borderRadius: 6,
  background: "transparent", color: "inherit",
};

export function FilterConditionRows({ fields, module, rows, onChange, logic, onLogicChange }: Props) {
  const t = useT();
  const candidateFields = buildCandidateFields(t, fields, module);
  const fieldByName = new Map(fields.map(f => [f.field_name, f]));

  function patchRow(i: number, patch: Partial<RowState>) {
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function addRow() { onChange([...rows, newRow()]); }
  function removeRow(i: number) { onChange(rows.filter((_, idx) => idx !== i)); }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div className="small" style={{ fontWeight: 600 }}>
          {t("viewbuilder.conditions")}
        </div>
        <div className="row" style={{ gap: 4 }}>
          <button
            className={logic === "all" ? "primary" : "ghost"}
            onClick={() => onLogicChange("all")}
            style={{ fontSize: 14, padding: "3px 10px" }}
          >
            {t("viewbuilder.logic_all")}
          </button>
          <button
            className={logic === "any" ? "primary" : "ghost"}
            onClick={() => onLogicChange("any")}
            style={{ fontSize: 14, padding: "3px 10px" }}
          >
            {t("viewbuilder.logic_any")}
          </button>
        </div>
      </div>

      {rows.length === 0 && (
        <div className="muted small" style={{
          padding: 12, border: "1px dashed var(--border)", borderRadius: 6, marginBottom: 8,
        }}>
          {t("viewbuilder.no_conditions")}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((row, i) => {
          const meta = fieldByName.get(row.field);
          const isSelect = meta?.field_type === "select" || meta?.field_type === "picklist";
          const noValue = NO_VALUE_OPS.has(row.operator);
          const showValueInput = !noValue && row.dynKind === "literal";
          const showDays = !noValue && row.dynKind === "last_n_days";
          const selectOpts = isSelect ? localizedSelectOptions(meta?.options, meta?.field_config) : [];
          const allowedKinds = dynKindsForField(row.field, meta);
          return (
            <div
              key={i}
              style={{
                display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center",
                padding: 8, border: "1px solid var(--border)", borderRadius: 6,
              }}
            >
              <select
                value={row.field}
                onChange={e => {
                  const nf = e.target.value;
                  const allowed = dynKindsForField(nf, fieldByName.get(nf));
                  // Reset value-kind if it no longer applies to the new field
                  // (e.g. switching off a user field drops "我的团队").
                  patchRow(i, allowed.includes(row.dynKind)
                    ? { field: nf }
                    : { field: nf, dynKind: "literal", value: "" });
                }}
                style={{ ...inputStyle, flex: "1 1 130px", minWidth: 110 }}
              >
                <option value="">{t("viewbuilder.field")}</option>
                {candidateFields.map(c => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>

              <select
                value={row.operator}
                onChange={e => patchRow(i, { operator: e.target.value })}
                style={{ ...inputStyle, flex: "0 0 auto", width: "auto" }}
              >
                {OPERATORS.map(op => (
                  <option key={op} value={op}>{t(`op.${op}`)}</option>
                ))}
              </select>

              {!noValue && allowedKinds.length > 1 && (
                <select
                  value={row.dynKind}
                  onChange={e => patchRow(i, { dynKind: e.target.value as DynKind })}
                  style={{ ...inputStyle, flex: "0 0 auto", width: "auto" }}
                >
                  {allowedKinds.map(k => (
                    <option key={k} value={k}>{t(`dynval.${k}`)}</option>
                  ))}
                </select>
              )}

              {showValueInput && isSelect && (
                <select
                  value={row.value}
                  onChange={e => patchRow(i, { value: e.target.value })}
                  style={{ ...inputStyle, flex: "1 1 120px", minWidth: 100 }}
                >
                  <option value="">{t("viewbuilder.value_ph")}</option>
                  {selectOpts.map(o => (
                    <option key={o.value} value={o.value}>{o.text}</option>
                  ))}
                </select>
              )}
              {showValueInput && !isSelect && (
                <input
                  type="text"
                  value={row.value}
                  onChange={e => patchRow(i, { value: e.target.value })}
                  placeholder={t("viewbuilder.value_ph")}
                  style={{ ...inputStyle, flex: "1 1 120px", minWidth: 100 }}
                />
              )}
              {showDays && (
                <input
                  type="number"
                  value={row.value}
                  onChange={e => patchRow(i, { value: e.target.value })}
                  placeholder={t("dynval.last_n_days_ph")}
                  style={{ ...inputStyle, flex: "0 0 auto", width: 96 }}
                />
              )}

              <button
                className="ghost"
                onClick={() => removeRow(i)}
                style={{ fontSize: 14, padding: "4px 8px", color: "var(--danger)", marginLeft: "auto" }}
                title={t("listcfg.remove")}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      <button className="ghost" onClick={addRow} style={{ fontSize: 14, marginTop: 8 }}>
        {t("viewbuilder.add_condition")}
      </button>
    </div>
  );
}

/** Convert rows → ViewFilter[] for saving/sending. */
export function rowsToFilters(rows: RowState[]): { field: string; operator: string; value: unknown }[] {
  return rows
    .filter(r => r.field)
    .map(r => ({
      field: r.field,
      operator: r.operator,
      value: NO_VALUE_OPS.has(r.operator) ? null : encodeValue(r),
    }));
}

/** Convert stored ViewFilter[] → rows for editing. */
export function filtersToRows(filters: { field: string; operator: string; value: unknown }[]): RowState[] {
  return (filters || []).map(f => {
    const dec = decodeValue(f.value);
    return { field: f.field, operator: f.operator || "eq", dynKind: dec.dynKind, value: dec.value };
  });
}
