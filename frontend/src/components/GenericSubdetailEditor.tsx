/**
 * GenericSubdetailEditor — 通用子明细行编辑器(P4)。
 *
 * 按子明细的字段元数据动态渲染列(text/number/date/picklist/...),
 * 行可增删。值通过 onChange 回到父组件,父组件保存时放进
 * ``details: {<子明细名>: rows}`` 提交(后端 save._save_subdetails 整组替换)。
 *
 * 区别于产品专用的 LineItemsEditor(写死 products_id/quantity/price):
 * 本组件不假设任何字段,纯元数据驱动,适配任意 generic 子明细。
 */
import { useEffect, useState } from "react";
import { useT, useLocale } from "@/lib/i18n";
import { listUsersPicker, type SubdetailDef } from "@/lib/api";
import { evalFormulaPreview } from "@/lib/formulaPreview";
import { localizedSelectOptions } from "@/lib/crmLabels";

type Row = Record<string, unknown>;

const USER_TYPES = ["user", "ownerby"];

const NUMERIC_TYPES = ["number", "currency", "float", "int", "percent", "formula"];
function isNumericType(fieldType: string): boolean {
  return NUMERIC_TYPES.includes((fieldType || "").toLowerCase());
}

function inputType(fieldType: string): string {
  const t = (fieldType || "").toLowerCase();
  if (["number", "currency", "float", "int", "percent"].includes(t)) return "number";
  if (t === "date") return "date";
  if (t === "datetime") return "datetime-local";
  if (t === "email") return "email";
  if (t === "url") return "url";
  return "text";
}

function fmtNum(n: number): string {
  // 整数不带小数;有小数保留最多 2 位。
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

export function GenericSubdetailEditor({
  def, value, onChange,
}: {
  def: SubdetailDef;
  value: Row[];
  onChange: (rows: Row[]) => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const rows = value || [];

  const setCell = (i: number, field: string, v: unknown) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, [field]: v } : r)));
  const addRow = () => onChange([...rows, {}]);
  const rmRow = (i: number) => onChange(rows.filter((_, j) => j !== i));

  const cols = def.fields;

  // ``user`` / ``ownerby`` columns are FK-to-user (integer user id in the DB).
  // Without a picker the cell fell back to a free-text <input>, so a typed name
  // like "ddd" was sent to an integer column → save 500. Fetch the user list
  // once (only when such a column exists) and render a <select> of users.
  const hasUserCol = cols.some(c => USER_TYPES.includes((c.field_type || "").toLowerCase()));
  const [users, setUsers] = useState<{ id: number; name: string }[]>([]);
  useEffect(() => {
    if (!hasUserCol) return;
    let alive = true;
    listUsersPicker("", 200)
      .then(list => { if (alive) setUsers(list || []); })
      .catch(() => { /* leave empty; cell shows no options but won't crash */ });
    return () => { alive = false; };
  }, [hasUserCol]);

  // 行内公式列的显示值:用同行其它字段实时预览(后端保存时按 Decimal 权威重算);
  // 预览失败(空行/坏表达式)回退到已存值。
  const formulaValue = (c: typeof cols[number], r: Row): number | null => {
    const expr = c.field_config?.formula?.expression;
    if (!expr) return null;
    const preview = evalFormulaPreview(expr, r);
    if (preview !== null) return preview;
    const stored = Number(r[c.field_name]);
    return Number.isFinite(stored) ? stored : null;
  };

  return (
    <div className="kacrm-subdetail-editor" style={{ marginTop: 8 }}>
      <div style={{ fontSize: 14, color: "var(--muted)", marginBottom: 6 }}>
        {def.title || def.name}
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="kacrm-subdetail-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr>
              {cols.map(c => (
                <th key={c.field_name} scope="col" style={th}>{c.field_label || c.field_name}</th>
              ))}
              <th style={{ ...th, width: 32 }} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {cols.map(c => {
                  const ft = (c.field_type || "").toLowerCase();
                  const cur = r[c.field_name];
                  if (ft === "picklist") {
                    return (
                      <td key={c.field_name} data-label={c.field_label || c.field_name} style={td}>
                        <select aria-label={c.field_label || c.field_name} value={String(cur ?? "")}
                          onChange={e => setCell(i, c.field_name, e.target.value)}
                          style={cell}>
                          <option value="" />
                          {localizedSelectOptions(c.options, c.field_config, locale).map(o =>
                            <option key={o.value} value={o.value}>{o.text}</option>)}
                        </select>
                      </td>
                    );
                  }
                  if (USER_TYPES.includes(ft)) {
                    return (
                      <td key={c.field_name} data-label={c.field_label || c.field_name} style={td}>
                        <select aria-label={c.field_label || c.field_name} value={String(cur ?? "")}
                          onChange={e => setCell(i, c.field_name,
                            e.target.value === "" ? null : Number(e.target.value))}
                          style={cell}>
                          <option value="" />
                          {users.map(u =>
                            <option key={u.id} value={u.id}>{u.name}</option>)}
                        </select>
                      </td>
                    );
                  }
                  if (ft === "boolean") {
                    return (
                      <td key={c.field_name} data-label={c.field_label || c.field_name} style={{ ...td, textAlign: "center" }}>
                        <input aria-label={c.field_label || c.field_name} type="checkbox" checked={!!cur}
                          onChange={e => setCell(i, c.field_name, e.target.checked)} />
                      </td>
                    );
                  }
                  if (ft === "formula") {
                    // 公式列只读:显示实时预览值(ƒ 提示),不可编辑。
                    const fv = formulaValue(c, r);
                    return (
                      <td key={c.field_name} data-label={c.field_label || c.field_name} style={td}>
                        <input aria-label={c.field_label || c.field_name} type="text" readOnly tabIndex={-1}
                          value={fv === null ? "" : fmtNum(fv)}
                          title={c.field_config?.formula?.expression || ""}
                          style={{ ...cell, ...roCell }} />
                      </td>
                    );
                  }
                  if (c.is_readonly) {
                    // 系统/只读字段:展示但不可改(如服务端算出的列)。
                    return (
                      <td key={c.field_name} data-label={c.field_label || c.field_name} style={td}>
                        <input aria-label={c.field_label || c.field_name} type={inputType(c.field_type)} readOnly tabIndex={-1}
                          value={String(cur ?? "")} style={{ ...cell, ...roCell }} />
                      </td>
                    );
                  }
                  return (
                    <td key={c.field_name} data-label={c.field_label || c.field_name} style={td}>
                      <input aria-label={c.field_label || c.field_name} type={inputType(c.field_type)}
                        value={String(cur ?? "")}
                        onChange={e => {
                          const it = inputType(c.field_type);
                          const raw = e.target.value;
                          // Empty numeric/date cells must become NULL, not "":
                          // an empty string bound to an integer/numeric/date
                          // column makes Postgres 500 ("invalid input syntax").
                          let v: unknown = raw;
                          if (it === "number") v = raw === "" ? null : Number(raw);
                          else if (it === "date" || it === "datetime-local") v = raw === "" ? null : raw;
                          setCell(i, c.field_name, v);
                        }}
                        style={cell} />
                    </td>
                  );
                })}
                <td style={{ ...td, textAlign: "center" }}>
                  <button type="button" aria-label={t("common.delete")} onClick={() => rmRow(i)} style={rmBtn}>×</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={cols.length + 1}
                style={{ ...td, color: "var(--muted)", textAlign: "center", padding: 10 }}>
                {t("crm.subdetail.empty")}
              </td></tr>
            )}
          </tbody>
          {rows.length > 0 && cols.some(c => isNumericType(c.field_type)) && (
            <tfoot>
              <tr>
                {cols.map((c, ci) => {
                  if (isNumericType(c.field_type)) {
                    const isFormula = (c.field_type || "").toLowerCase() === "formula";
                    const sum = rows.reduce((s, r) => {
                      const v = isFormula
                        ? (formulaValue(c, r) ?? 0)
                        : (Number(r[c.field_name]) || 0);
                      return s + v;
                    }, 0);
                    return <td key={c.field_name} data-label={c.field_label || c.field_name} style={footTd}>{fmtNum(sum)}</td>;
                  }
                  // 第一列(非数值)放「合计」标签
                  return <td key={c.field_name} data-label={c.field_label || c.field_name} style={footTd}>
                    {ci === 0 ? t("crm.subdetail.total") : ""}
                  </td>;
                })}
                <td style={footTd} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <button type="button" onClick={addRow} style={addBtn}>
        + {t("crm.subdetail.add_row")}
      </button>
    </div>
  );
}

const footTd: React.CSSProperties = {
  padding: "5px 6px", borderTop: "2px solid var(--border)",
  fontWeight: 600, fontSize: 14, whiteSpace: "nowrap",
};

const th: React.CSSProperties = {
  textAlign: "left", padding: "4px 6px", borderBottom: "1px solid var(--border)",
  color: "var(--muted)", fontWeight: 500, whiteSpace: "nowrap",
};
const td: React.CSSProperties = { padding: "3px 4px", borderBottom: "1px solid var(--border)" };
const cell: React.CSSProperties = {
  width: "100%", minWidth: 80, padding: "4px 6px", background: "var(--bg, #0e0f13)",
  color: "inherit", border: "1px solid var(--border)", borderRadius: 4, fontSize: 14,
  boxSizing: "border-box",
};
const roCell: React.CSSProperties = {
  background: "var(--panel, #16181d)", color: "var(--muted)", cursor: "default",
};
const rmBtn: React.CSSProperties = {
  background: "transparent", color: "var(--muted)", border: "none",
  cursor: "pointer", fontSize: 16,
};
const addBtn: React.CSSProperties = {
  marginTop: 6, padding: "5px 10px", background: "transparent",
  color: "var(--accent, #4c8dff)", border: "1px dashed var(--border)",
  borderRadius: 6, cursor: "pointer", fontSize: 14,
};
